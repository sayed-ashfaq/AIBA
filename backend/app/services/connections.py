"""Persistence for saved connections and schema annotations.

Everything here talks to *our* metadata store. Actually reaching out to a user's target database —
connecting, introspecting, running SQL — lives in app.agents.subagents.sql_agent.db, which this module calls
into but never the other way around. The live engine for whoever is currently connected is held by
app.services.connection_registry.

Every function takes the signed-in user and filters on their id. A connection is private to the
account that created it; there is no path here that reads a row without checking ownership.
"""

import uuid
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.subagents.sql_agent import db
from app.agents.subagents.sql_agent.db import DbContext
from app.core.crypto import decrypt, encrypt
from app.core.exceptions import (
    ConnectionNameTakenError,
    ConnectionNotFoundError,
    ConnectionUnreachableError,
    InvalidAnnotationError,
    NoActiveConnectionError,
)
from app.core.logging import get_logger
from app.db.models import SavedConnection, SchemaAnnotation, User
from app.services import connection_registry
from app.services.connection_registry import ActiveConnection

logger = get_logger(__name__)


async def _get_owned(session: AsyncSession, user: User, connection_id: uuid.UUID) -> SavedConnection:
    """The user's connection, or 404. Never session.get() by primary key alone — that would return
    rows belonging to other accounts."""
    row = await session.scalar(
        select(SavedConnection).where(
            SavedConnection.id == connection_id,
            SavedConnection.user_id == user.id,
        )
    )
    if row is None:
        # deliberately the same error as a genuinely unknown id, so this can't be used to test
        # whether some other account owns a given connection
        raise ConnectionNotFoundError
    return row


# --- active connection ---------------------------------------------------------------------------


async def _activate(session: AsyncSession, user: User, row: SavedConnection, connection: db.Connection):
    """Put a live connection in the registry and record the choice so a restart can rebuild it."""
    entry = connection_registry.set_active(user.id, row.id, connection)
    if user.active_connection_id != row.id:
        user.active_connection_id = row.id
        await session.commit()
    return entry


async def get_active(session: AsyncSession, user: User) -> Optional[ActiveConnection]:
    """The user's live connection, rebuilding it if it isn't in the registry.

    A miss is normal, not exceptional: the process restarted, or the entry aged out. The durable
    `users.active_connection_id` is the source of truth, so reconnecting here is what makes those
    invisible to the user. Raises if the target database is unreachable.
    """
    entry = connection_registry.get(user.id)
    if entry is not None:
        return entry
    if user.active_connection_id is None:
        return None

    row = await session.scalar(
        select(SavedConnection).where(
            SavedConnection.id == user.active_connection_id,
            SavedConnection.user_id == user.id,
        )
    )
    if row is None:
        # the connection was deleted out from under the pointer — SET NULL should have handled it,
        # so this is belt and braces rather than an expected path
        user.active_connection_id = None
        await session.commit()
        return None

    logger.info("rebuilding connection '%s' for user %s", row.name, user.id)
    connection = await db.build_connection(row.db_type, decrypt(row.url_encrypted), row.dbname)
    return await _activate(session, user, row, connection)


async def require_active(session: AsyncSession, user: User) -> ActiveConnection:
    entry = await get_active(session, user)
    if entry is None:
        raise NoActiveConnectionError
    return entry


# --- connections ---------------------------------------------------------------------------------


async def save_connection(
    session: AsyncSession,
    user: User,
    name: str,
    db_type: db.DBType,
    host: Optional[str] = None,
    port: Optional[int] = None,
    user_name: Optional[str] = None,
    password: Optional[str] = None,
    dbname: Optional[str] = None,
    url: Optional[str] = None,
) -> dict:
    sqlalchemy_url, resolved_dbname = db.resolve_url(db_type, host, port, user_name, password, dbname, url)
    # connect before inserting: a connection we can't reach isn't worth saving, and this surfaces
    # bad credentials as an error on the save itself rather than on some later activate
    connection = await db.build_connection(db_type, sqlalchemy_url, resolved_dbname)

    row = SavedConnection(
        user_id=user.id,
        name=name,
        db_type=db_type,
        url_encrypted=encrypt(sqlalchemy_url),
        dbname=resolved_dbname,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        connection.engine.dispose()  # nothing will ever reference it now
        raise ConnectionNameTakenError from exc

    await _activate(session, user, row, connection)
    logger.info("user %s saved and activated connection '%s' (id=%s)", user.id, name, row.id)
    return {"id": row.id, "name": name, "db_type": db_type, "dbname": resolved_dbname}


async def list_connections(session: AsyncSession, user: User) -> list[dict]:
    rows = (
        await session.scalars(
            select(SavedConnection)
            .where(SavedConnection.user_id == user.id)
            .order_by(SavedConnection.created_at)
        )
    ).all()
    # from the column, not the registry: a connection stays "active" across a restart even before
    # anything has forced it to be rebuilt
    active_id = user.active_connection_id
    return [
        {
            "id": row.id,
            "name": row.name,
            "db_type": row.db_type,
            "dbname": row.dbname,
            "active": row.id == active_id,
        }
        for row in rows
    ]


async def activate_connection(session: AsyncSession, user: User, connection_id: uuid.UUID) -> dict:
    row = await _get_owned(session, user, connection_id)
    connection = await db.build_connection(row.db_type, decrypt(row.url_encrypted), row.dbname)
    await _activate(session, user, row, connection)
    logger.info("user %s activated connection '%s' (id=%s)", user.id, row.name, row.id)
    return {"id": row.id, "name": row.name, "db_type": row.db_type, "dbname": row.dbname}


async def delete_connection(session: AsyncSession, user: User, connection_id: uuid.UUID) -> None:
    row = await _get_owned(session, user, connection_id)
    was_active = user.active_connection_id == row.id

    await session.delete(row)
    if was_active:
        # the FK is ON DELETE SET NULL, but the in-session User object wouldn't know that until it
        # was refreshed — set it here so the rest of this request sees the truth
        user.active_connection_id = None
    await session.commit()

    if was_active:
        connection_registry.clear(user.id)
    logger.info("user %s deleted connection id=%s", user.id, connection_id)


# --- annotations ---------------------------------------------------------------------------------


def _annotation_to_dict(row: SchemaAnnotation) -> dict:
    return {
        "id": row.id,
        "connection_id": row.connection_id,
        "schema_name": row.schema_name or None,
        "table_name": row.table_name,
        "column_name": row.column_name or None,
        "comment": row.comment,
        "updated_at": row.updated_at,
    }


async def list_annotations(session: AsyncSession, user: User, connection_id: uuid.UUID) -> list[dict]:
    await _get_owned(session, user, connection_id)
    rows = await session.scalars(
        select(SchemaAnnotation).where(SchemaAnnotation.connection_id == connection_id)
    )
    return [_annotation_to_dict(row) for row in rows]


async def _annotations_map(session: AsyncSession, connection_id: uuid.UUID) -> dict[tuple[str, str], str]:
    """(qualified_table_name, column_name) -> comment, column_name='' for a table-level comment.
    Keyed the same way render_schema_text looks things up. Ownership is checked by the caller, which
    had to resolve the connection to get here."""
    rows = await session.execute(
        select(
            SchemaAnnotation.schema_name,
            SchemaAnnotation.table_name,
            SchemaAnnotation.column_name,
            SchemaAnnotation.comment,
        ).where(SchemaAnnotation.connection_id == connection_id)
    )
    return {
        (db.qualify(schema_name or None, table_name), column_name): comment
        for schema_name, table_name, column_name, comment in rows
    }


async def upsert_annotation(
    session: AsyncSession,
    user: User,
    connection_id: uuid.UUID,
    table_name: str,
    comment: str,
    schema_name: Optional[str] = None,
    column_name: Optional[str] = None,
) -> dict:
    await _get_owned(session, user, connection_id)

    # get_active(), not a bare registry peek: after a restart the registry is empty, and peeking
    # would silently skip the check below rather than rehydrate. Best-effort, so an unreachable
    # database means no validation — never a failed write.
    try:
        entry = await get_active(session, user)
    except ConnectionUnreachableError:
        entry = None
    _validate_against_active(entry, connection_id, table_name, schema_name, column_name)

    row = await session.scalar(
        select(SchemaAnnotation).where(
            SchemaAnnotation.connection_id == connection_id,
            SchemaAnnotation.schema_name == (schema_name or ""),
            SchemaAnnotation.table_name == table_name,
            SchemaAnnotation.column_name == (column_name or ""),
        )
    )
    if row is None:
        row = SchemaAnnotation(
            connection_id=connection_id,
            schema_name=schema_name or "",
            table_name=table_name,
            column_name=column_name or "",
            comment=comment,
        )
        session.add(row)
    else:
        row.comment = comment  # updated_at refreshes itself via the column's onupdate

    await session.commit()
    await session.refresh(row)
    return _annotation_to_dict(row)


def _validate_against_active(
    entry: Optional[ActiveConnection],
    connection_id: uuid.UUID,
    table_name: str,
    schema_name: Optional[str],
    column_name: Optional[str],
) -> None:
    """Best-effort check against the live schema: if this connection happens to be the user's active
    one, catch a wrong/missing schema_name now — e.g. forgetting schema_name='public' on postgres —
    instead of silently writing a comment that will never match anything in render_schema_text.
    A connection that isn't loaded has no schema in memory, so there is nothing to check against."""
    if entry is None or entry.connection_id != connection_id:
        return

    qualified = db.qualify(schema_name, table_name)
    table = entry.connection.tables.get(qualified)
    if table is None:
        raise InvalidAnnotationError(
            f"no table '{qualified}' in the active connection's schema — table names are "
            f"schema-qualified for postgres (e.g. schema_name='public', table_name='rental')"
        )
    if column_name and column_name not in {c.name for c in table.columns}:
        raise InvalidAnnotationError(f"no column '{column_name}' on table '{qualified}'")


async def delete_annotation(
    session: AsyncSession, user: User, connection_id: uuid.UUID, annotation_id: uuid.UUID
) -> None:
    await _get_owned(session, user, connection_id)
    result = await session.execute(
        delete(SchemaAnnotation).where(
            SchemaAnnotation.id == annotation_id,
            SchemaAnnotation.connection_id == connection_id,
        )
    )
    if result.rowcount == 0:
        raise ConnectionNotFoundError(f"no annotation with id {annotation_id} for this connection")
    await session.commit()


# --- schema ---------------------------------------------------------------------------------------


async def get_active_schema_text(session: AsyncSession, user: User) -> str:
    """Schema of the user's active connection, annotations merged in. Raises if nothing is active."""
    entry = await require_active(session, user)
    annotations = await _annotations_map(session, entry.connection_id)
    return db.render_schema_text(entry.connection.tables, annotations)


async def get_active_db_context(session: AsyncSession, user: User) -> Optional[DbContext]:
    """Same as get_active_schema_text but returns None instead of raising when the user has no
    connection — the chat endpoint resolves this for every message, including ones that never reach
    the SQL agent, so "no connection yet" must stay a valid state here."""
    try:
        entry = await get_active(session, user)
    except ConnectionUnreachableError:
        # the saved connection exists but its database is unreachable right now. Chat still works
        # for anything that doesn't need SQL; the SQL agent will report the missing connection.
        logger.warning("could not rebuild active connection for user %s", user.id, exc_info=True)
        return None
    if entry is None:
        return None

    annotations = await _annotations_map(session, entry.connection_id)
    return DbContext(
        connection=entry.connection,
        schema_text=db.render_schema_text(entry.connection.tables, annotations),
    )
