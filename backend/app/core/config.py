from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

# drivers that mean "postgres, synchronously" — rewritten to asyncpg so .env can stay in the
# ordinary postgres:// form that psql and every other tool understands
_SYNC_POSTGRES_DRIVERS = {"postgresql", "postgres", "postgresql+psycopg2", "postgresql+psycopg"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: str

    # one model id per agent, so each can be tuned independently. llama-3.3-70b-versatile was tried
    # for main_agent first, but it malforms Groq's structured tool-calling once the orchestrator's
    # tool surface gets this large (task, write_todos, plus the built-in filesystem tools) — Groq
    # rejects the call with a 400 tool_use_failed. gpt-oss-120b handles it reliably.
    main_agent_model: str = "openai/gpt-oss-120b"
    sql_agent_model: str = "openai/gpt-oss-120b"
    # picks between pre-validated chart options — a small judgement call on a short prompt, so it
    # has no use for a larger model than this
    visualizer_model: str = "llama-3.3-70b-versatile"

    # app's own metadata store (saved DB connections), separate from any target DB
    metadata_database_url: str
    credentials_encryption_key: str

    # --- auth ---------------------------------------------------------------------------------

    session_cookie_name: str = "session"
    session_ttl_days: int = 30
    # sliding expiry: a session this close to expiring gets extended on use, so active users are
    # never logged out mid-work while idle sessions still age out on schedule
    session_refresh_days: int = 7

    # False in local dev because the frontend talks to http://localhost. MUST be True anywhere the
    # app is reachable over a network, or the cookie travels in plaintext.
    cookie_secure: bool = False
    # where the Google callback sends the browser once the session cookie is set
    frontend_url: str = "http://localhost:5173"

    # signs the short-lived cookie holding the OAuth state parameter (CSRF protection for the
    # Google redirect). Unrelated to session tokens, which are random and stored server-side.
    session_secret_key: str

    # optional: Google sign-in stays switched off (endpoints return 503) until both are set, so the
    # app boots for anyone who hasn't registered OAuth credentials yet
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None

    @property
    def google_oauth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @field_validator("metadata_database_url")
    @classmethod
    def _force_async_driver(cls, value: str) -> str:
        """Our metadata store is accessed only through the async engine, and asyncpg is the only
        driver that works there. Normalising here means one URL in .env instead of a sync copy for
        Alembic and an async copy for the app, which drift apart the moment one is edited."""
        url = make_url(value)
        if url.drivername in _SYNC_POSTGRES_DRIVERS:
            url = url.set(drivername="postgresql+asyncpg")
        return url.render_as_string(hide_password=False)


settings = Settings()
