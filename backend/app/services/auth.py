"""Signup, login, sessions and Google account linking.

Pure persistence + policy: no FastAPI types cross this boundary. The router owns cookies and
redirects, this module owns what is true about an account.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    AccountDisabledError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    OAuthFailedError,
)
from app.core.logging import get_logger
from app.core.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
    verify_password_dummy,
)
from app.db.models import OAuthAccount, Session, User

logger = get_logger(__name__)

GOOGLE_PROVIDER = "google"


def normalise_email(email: str) -> str:
    """Lower-case and strip. Applied on every read and write so an account can only ever be found
    the same way it was stored — the DB unique index is case-sensitive and would happily accept
    both Sayed@example.com and sayed@example.com as separate rows without this."""
    return email.strip().lower()


# --- sessions -----------------------------------------------------------------------------------


async def create_session(
    session: AsyncSession,
    user: User,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> str:
    """Returns the raw token, which the caller puts in a cookie. Only its hash is persisted, so
    this is the one and only moment the token is knowable server-side."""
    token = generate_session_token()
    session.add(
        Session(
            user_id=user.id,
            token_hash=hash_session_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.session_ttl_days),
            user_agent=(user_agent or None) and user_agent[:512],
            ip_address=ip_address,
        )
    )
    await session.commit()
    return token


async def resolve_session(session: AsyncSession, token: str) -> Optional[User]:
    """Token -> the user it belongs to, or None if it is unknown, expired, or the account is off.

    Returning None rather than raising for every failure mode keeps this usable for optional
    authentication; the dependency layer decides when None is fatal.
    """
    row = await session.scalar(
        select(Session).where(Session.token_hash == hash_session_token(token))
    )
    if row is None:
        return None

    now = datetime.now(timezone.utc)
    if row.expires_at <= now:
        # drop it on sight rather than leaving dead rows to accumulate — this is the only place
        # every session is guaranteed to be revisited
        await session.delete(row)
        await session.commit()
        return None

    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None

    await _touch(session, row, now)
    return user


async def _touch(session: AsyncSession, row: Session, now: datetime) -> None:
    """Sliding expiry. Extending only when the session is within `session_refresh_days` of expiring
    means one write every few days per user instead of one on every single request."""
    row.last_used_at = now
    if row.expires_at - now < timedelta(days=settings.session_refresh_days):
        row.expires_at = now + timedelta(days=settings.session_ttl_days)
    await session.commit()


async def revoke_session(session: AsyncSession, token: str) -> None:
    await session.execute(delete(Session).where(Session.token_hash == hash_session_token(token)))
    await session.commit()


async def revoke_all_sessions(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Sign out every browser — the point of keeping sessions server-side. Used after a password
    change, or by the user when they think a device was compromised."""
    result = await session.execute(delete(Session).where(Session.user_id == user_id))
    await session.commit()
    return result.rowcount


async def _purge_expired(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Opportunistic cleanup at login, which is often enough to stop the table growing without
    needing a scheduled job."""
    await session.execute(
        delete(Session).where(Session.user_id == user_id, Session.expires_at <= datetime.now(timezone.utc))
    )


# --- email + password ---------------------------------------------------------------------------


async def signup(session: AsyncSession, email: str, password: str, full_name: Optional[str] = None) -> User:
    email = normalise_email(email)

    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=(full_name or "").strip() or None,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # this does reveal that the address is taken. Unavoidable without an email-confirmation
        # flow, and the right trade for an internal tool where the alternative is a signup that
        # silently appears to succeed.
        raise EmailAlreadyRegisteredError from exc

    logger.info("signed up user %s", user.id)
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> User:
    user = await session.scalar(select(User).where(User.email == normalise_email(email)))

    if user is None or user.password_hash is None:
        # spend the same ~100ms a real argon2 verify costs, so response time doesn't distinguish
        # "no such account" from "wrong password". The second case is a Google-only account, where
        # revealing that it exists would be just as much of a leak.
        verify_password_dummy()
        raise InvalidCredentialsError

    if not verify_password(password, user.password_hash):
        raise InvalidCredentialsError

    if not user.is_active:
        raise AccountDisabledError

    await _purge_expired(session, user.id)
    await session.commit()
    return user


# --- google ---------------------------------------------------------------------------------------


async def upsert_google_user(session: AsyncSession, claims: dict) -> User:
    """Resolve Google's ID-token claims to a local user, creating or linking as needed."""
    subject = claims.get("sub")
    email = claims.get("email")
    if not subject or not email:
        raise OAuthFailedError("Google did not return an email address")

    # Google's own verification of the address. Everything below trusts it, so refuse outright if
    # it is absent — otherwise an unverified claim could be used to seize a matching local account.
    if not claims.get("email_verified"):
        raise OAuthFailedError("your Google account's email address is not verified")

    email = normalise_email(email)

    # 1. seen this exact Google identity before -> that user, regardless of email changes since
    link = await session.scalar(
        select(OAuthAccount).where(
            OAuthAccount.provider == GOOGLE_PROVIDER,
            OAuthAccount.provider_account_id == subject,
        )
    )
    if link is not None:
        user = await session.get(User, link.user_id)
        if user is None:
            raise OAuthFailedError
        if not user.is_active:
            raise AccountDisabledError
        return await _refresh_profile(session, user, claims)

    # 2. a local account already owns this address -> link, don't duplicate. Safe because Google
    #    has just attested the address, which is exactly the proof a signup would have demanded.
    user = await session.scalar(select(User).where(User.email == email))
    if user is not None:
        if not user.is_active:
            raise AccountDisabledError
        session.add(OAuthAccount(user_id=user.id, provider=GOOGLE_PROVIDER, provider_account_id=subject))
        user.email_verified = True
        logger.info("linked google identity to existing user %s", user.id)
        return await _refresh_profile(session, user, claims)

    # 3. nobody here yet -> new account, with no password until they choose to set one
    user = User(
        email=email,
        password_hash=None,
        full_name=claims.get("name"),
        avatar_url=claims.get("picture"),
        email_verified=True,
    )
    session.add(user)
    await session.flush()  # assign user.id before the FK below references it
    session.add(OAuthAccount(user_id=user.id, provider=GOOGLE_PROVIDER, provider_account_id=subject))
    await session.commit()
    logger.info("created user %s via google", user.id)
    return user


async def _refresh_profile(session: AsyncSession, user: User, claims: dict) -> User:
    """Keep name and avatar in step with Google, but never overwrite something the user set here
    with an empty value from the provider."""
    if claims.get("name") and not user.full_name:
        user.full_name = claims["name"]
    if claims.get("picture"):
        user.avatar_url = claims["picture"]
    await session.commit()
    return user
