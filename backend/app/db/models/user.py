import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # always stored lower-cased and stripped (see app.services.auth.normalise_email) so that
    # Sayed@Example.com and sayed@example.com can't become two accounts
    email: Mapped[str] = mapped_column(String(320), unique=True)

    # NULL for an account that only ever signs in through Google — a placeholder hash would be a
    # password nobody set, and this way "has a usable password" is just `password_hash is not None`
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # True immediately for Google sign-ups (Google has already verified it). Currently informational
    # for password sign-ups — nothing enforces it yet, but the column is here so turning on email
    # confirmation later doesn't need a migration.
    email_verified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # soft off-switch: revoking access shouldn't destroy the user's chats and connections
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))

    # which target database this user is currently pointed at. The live engine lives in
    # app.services.connection_registry (process memory); this column is what lets a restart — or an
    # eviction from that registry — rebuild it lazily instead of making the user re-activate.
    # SET NULL rather than CASCADE: deleting a connection must not delete its owner.
    active_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # use_alter: users and saved_connections reference each other, so this one constraint is
        # emitted as a separate ALTER TABLE to break the cycle at create time
        ForeignKey(
            "saved_connections.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_users_active_connection_id_saved_connections",
        ),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    oauth_accounts: Mapped[list["OAuthAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class OAuthAccount(Base):
    """A link between a local user and an external identity provider.

    A table rather than columns on `users` because it lets one account carry several providers at
    once, and adding a second provider later is an INSERT instead of a schema change.
    """

    __tablename__ = "oauth_accounts"
    __table_args__ = (
        # the identity Google asserts is (provider, sub) — this is what makes a repeat login find
        # the existing account instead of creating a duplicate
        UniqueConstraint("provider", "provider_account_id", name="uq_oauth_accounts_provider_account"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    # provider's immutable id for the user — Google's `sub`, never the email, which can change
    provider_account_id: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="oauth_accounts")


class Session(Base):
    """A logged-in browser.

    Only the SHA-256 of the token is stored. The raw token exists in the user's cookie and nowhere
    else, so a read-only leak of this table — a backup, a stray log line, injection elsewhere —
    cannot be replayed as a login. SHA-256 rather than argon2 is deliberate: the token is 256 bits
    of CSPRNG output, so there is no guessable input to slow down, and this runs on every request.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    # context for an "active sessions" screen and for spotting a stolen cookie
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="sessions")
