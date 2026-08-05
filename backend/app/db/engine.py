from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# our own metadata store — never a user's target database. settings normalises the URL to the
# asyncpg driver, so this is the only engine in the app that talks to nl2sql_meta.
engine = create_async_engine(settings.metadata_database_url, pool_pre_ping=True)

# expire_on_commit=False: with the default, touching any attribute after commit triggers a lazy
# refresh, which raises MissingGreenlet under asyncio. Keeping loaded values usable after commit
# lets services commit and then build a response from the same object.
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
