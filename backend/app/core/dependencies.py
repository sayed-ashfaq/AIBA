"""Request-scoped auth dependencies.

`CurrentUser` is the piece Module 3 attaches to /chat and /connections to scope them per user.
"""

from typing import Annotated, Optional

from fastapi import Depends, Request

from app.core.config import settings
from app.core.exceptions import NotAuthenticatedError
from app.db.models import User
from app.db.session import SessionDep
from app.services import auth as auth_service


async def get_current_user_optional(request: Request, session: SessionDep) -> Optional[User]:
    """The signed-in user, or None. For endpoints that work either way."""
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    return await auth_service.resolve_session(session, token)


async def get_current_user(
    user: Annotated[Optional[User], Depends(get_current_user_optional)],
) -> User:
    """The signed-in user, or 401. Built on the optional form so the cookie is read and the session
    resolved exactly once per request even when both are in play."""
    if user is None:
        raise NotAuthenticatedError
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[Optional[User], Depends(get_current_user_optional)]
