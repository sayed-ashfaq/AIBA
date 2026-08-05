import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from app.core.config import settings
from app.core.dependencies import CurrentUser
from app.core.exceptions import OAuthFailedError, OAuthNotConfiguredError
from app.core.logging import get_logger
from app.core.oauth import oauth
from app.db.models import User
from app.db.session import SessionDep
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger(__name__)


class SignupRequest(BaseModel):
    email: EmailStr
    # 8 is the floor, not a target. Length is what matters; complexity rules mostly produce
    # predictable substitutions, so there are none here.
    password: str = Field(min_length=8, max_length=128)
    full_name: Optional[str] = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    email_verified: bool
    # tells a frontend whether to offer "change password" or "set a password" on a Google-only
    # account, without ever exposing the hash itself
    has_password: bool
    created_at: datetime

    @classmethod
    def of(cls, user: User) -> "UserResponse":
        return cls(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            avatar_url=user.avatar_url,
            email_verified=user.email_verified,
            has_password=user.password_hash is not None,
            created_at=user.created_at,
        )


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        # httponly: unreadable from JavaScript, so an XSS bug can't exfiltrate the session
        httponly=True,
        # lax: sent on top-level navigation (needed for the Google redirect back) but not on
        # cross-site POSTs, which is what blocks CSRF
        samesite="lax",
        # HTTPS only — off in local dev, and must be on anywhere else
        secure=settings.cookie_secure,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    # the flags must match those used to set it, or the browser keeps the original cookie
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


async def _start_session(request: Request, response: Response, session, user: User) -> None:
    token = await auth_service.create_session(
        session,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_session_cookie(response, token)


@router.post("/signup", response_model=UserResponse, status_code=201)
async def signup(payload: SignupRequest, request: Request, response: Response, session: SessionDep) -> UserResponse:
    user = await auth_service.signup(session, payload.email, payload.password, payload.full_name)
    # signing up logs you in — a separate login round-trip here would be friction with no benefit
    await _start_session(request, response, session, user)
    return UserResponse.of(user)


@router.post("/login", response_model=UserResponse)
async def login(payload: LoginRequest, request: Request, response: Response, session: SessionDep) -> UserResponse:
    user = await auth_service.authenticate(session, payload.email, payload.password)
    await _start_session(request, response, session, user)
    return UserResponse.of(user)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, session: SessionDep) -> Response:
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        await auth_service.revoke_session(session, token)
    _clear_session_cookie(response)
    # 204 has no body, so the cookie header has to ride on a bare Response
    return Response(status_code=204, headers=response.headers)


@router.post("/logout-all")
async def logout_all(user: CurrentUser, response: Response, session: SessionDep) -> dict:
    """Sign out of every browser. Cheap only because sessions live in the database — a stateless
    JWT would need a denylist to do this at all."""
    count = await auth_service.revoke_all_sessions(session, user.id)
    _clear_session_cookie(response)
    return {"revoked": count}


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.of(user)


# --- google ---------------------------------------------------------------------------------------


@router.get("/google/login")
async def google_login(request: Request):
    """Send the browser to Google. Authlib stashes the OAuth `state` in the signed session cookie
    and checks it on the way back, which is what stops a forged callback."""
    if not settings.google_oauth_configured:
        raise OAuthNotConfiguredError

    # built from the running app's routes so it always matches whatever host/port we're on — this
    # exact string must also be registered in the Google Cloud console
    redirect_uri = request.url_for("google_callback")
    return await oauth.google.authorize_redirect(request, str(redirect_uri))


@router.get("/google/callback", name="google_callback")
async def google_callback(request: Request, session: SessionDep):
    """Google sends the user back here. Ends with a redirect to the frontend rather than JSON,
    because the browser — not fetch() — is what followed the OAuth flow."""
    if not settings.google_oauth_configured:
        raise OAuthNotConfiguredError

    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as exc:
        # covers a tampered/expired state, a denied consent screen, and network failures alike
        logger.warning("google callback failed: %s", exc)
        raise OAuthFailedError from exc

    claims = token.get("userinfo")
    if not claims:
        raise OAuthFailedError

    user = await auth_service.upsert_google_user(session, claims)

    redirect = RedirectResponse(url=settings.frontend_url, status_code=303)
    await _start_session(request, redirect, session, user)
    return redirect
