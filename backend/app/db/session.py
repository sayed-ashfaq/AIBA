from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yields a session without opening a transaction for the request's lifetime.

    A chat request spends 10-30s in LLM calls; a request-scoped `session.begin()` would hold a
    pooled connection open across all of it. Services commit explicitly instead, so connections
    go back to the pool as soon as the write is done.
    """
    async with session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
