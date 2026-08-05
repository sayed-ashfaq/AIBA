"""Google OAuth client.

Isolated from the router so that "is Google configured?" is answered in one place, and so adding a
second provider later means registering another client here rather than touching request handling.
"""

from authlib.integrations.starlette_client import OAuth

from app.core.config import settings

oauth = OAuth()

if settings.google_oauth_configured:
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        # discovery document: lets authlib fetch Google's current endpoints and signing keys itself
        # instead of us hard-coding URLs that Google rotates
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
