"""Which target database each user is currently pointed at, in process memory.

This replaces the single process-wide `_active` connection that used to live in
app.agents.sql_agent.db. With one global, activating a connection changed the database *every* user
of the server was querying; the registry makes "active" a per-user fact.

Two things live here rather than in the database because they cannot be serialised: the live
SQLAlchemy Engine (with its connection pool) and the introspected schema. The durable half — which
connection id a user chose — is `users.active_connection_id`, so anything evicted from here can be
rebuilt on the next request. Losing an entry costs one reconnect, never correctness.
"""

import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

from app.agents.sql_agent.db import Connection
from app.agents.sql_agent.schema_graph import SchemaGraph
from app.core.logging import get_logger

logger = get_logger(__name__)

# Each entry pins an open connection pool, so this cannot grow without bound. Well above the
# expected concurrent user count — eviction is a slow-path safety valve, not routine behaviour.
MAX_ENTRIES = 50


@dataclass
class ActiveConnection:
    connection_id: uuid.UUID
    connection: Connection
    # built on first request to a graph view and cached for exactly as long as the connection it
    # describes. Kept here rather than in a cache keyed by id(engine): ids are recycled after
    # garbage collection, so a disposed engine's key could silently return another user's graph.
    schema_graph: Optional[SchemaGraph] = None


# ordered by least-recently-used first, so eviction is popitem(last=False)
_entries: "OrderedDict[uuid.UUID, ActiveConnection]" = OrderedDict()


def get(user_id: uuid.UUID) -> Optional[ActiveConnection]:
    entry = _entries.get(user_id)
    if entry is not None:
        _entries.move_to_end(user_id)
    return entry


def set_active(user_id: uuid.UUID, connection_id: uuid.UUID, connection: Connection) -> ActiveConnection:
    """Point a user at a connection, disposing whatever they were on before.

    The dispose matters: build_connection() creates a brand new Engine every time, so without this
    each switch would strand a pool of open sockets to the previous database.
    """
    _dispose(_entries.pop(user_id, None))

    entry = ActiveConnection(connection_id=connection_id, connection=connection)
    _entries[user_id] = entry

    while len(_entries) > MAX_ENTRIES:
        evicted_user, evicted = _entries.popitem(last=False)
        logger.info("evicting cached connection for user %s (registry full)", evicted_user)
        _dispose(evicted)

    return entry


def clear(user_id: uuid.UUID) -> None:
    _dispose(_entries.pop(user_id, None))


def _dispose(entry: Optional[ActiveConnection]) -> None:
    if entry is None:
        return
    try:
        entry.connection.engine.dispose()
    except Exception:
        # a pool that fails to close cleanly is not worth failing the user's request over
        logger.warning("failed to dispose engine for connection %s", entry.connection_id, exc_info=True)
