"""Access to a *user's* target database: connecting, introspecting, running their SQL.

Nothing here touches our own metadata store — saving and loading connection records is
app.services.connections. The drivers used here (psycopg2, pymysql) are synchronous, so the
functions that reach the network are wrapped in a thread to keep the event loop free.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Literal, Optional

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine, make_url

from app.agents.sql_agent import rows
from app.core.exceptions import ConnectionUnreachableError, SQLExecutionError
from app.core.logging import get_logger

logger = get_logger(__name__)

DBType = Literal["postgres", "mysql"]

_DRIVER = {
    "postgres": "postgresql+psycopg2",
    "mysql": "mysql+pymysql",
}

# The ceiling on one result set. Enforced twice, deliberately: sql.apply_row_cap writes it into the
# query so the database itself stops early, and the fetch below repeats it in case a query somehow
# reaches here uncapped. 500 was enough while a result only ever became a paragraph of prose;
# charts need the rows behind the summary, and 5000 is roughly where a browser stops rendering
# them comfortably anyway.
MAX_ROWS = 5000
QUERY_TIMEOUT_MS = 10_000


@dataclass
class ColumnSchema:
    name: str
    type: str
    pk: bool


@dataclass
class ForeignKeySchema:
    columns: list[str]
    referred_table: str
    referred_columns: list[str]


@dataclass
class TableSchema:
    name: str  # qualified, e.g. "sales.customer"
    columns: list[ColumnSchema] = field(default_factory=list)
    foreign_keys: list[ForeignKeySchema] = field(default_factory=list)


@dataclass
class Connection:
    engine: Engine
    db_type: DBType
    dbname: str
    tables: dict[str, TableSchema]  # structural data only — expensive, cached for the connection's life


@dataclass(frozen=True)
class DbContext:
    """Everything the SQL agent needs in order to answer against one user's database, resolved by
    the router before the graph runs — agent nodes are synchronous and cannot await the annotations
    read, nor reach into the async registry.

    Lives here rather than in the service layer so app.agents.main_agent.state can name it in
    AgentState without an agent module importing a service. LangGraph resolves state type hints at
    runtime, so this import cannot be deferred behind TYPE_CHECKING.

    Carrying the live `connection` is what removes the last process-wide global: run_query used to
    reach for whichever database was active server-wide, which with more than one user meant
    answering questions against somebody else's data.
    """

    connection: Connection
    schema_text: str

    @property
    def db_type(self) -> str:
        return self.connection.db_type

    @property
    def db_name(self) -> str:
        return self.connection.dbname


# --- connecting -------------------------------------------------------------------------------


def resolve_url(
    db_type: DBType,
    host: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    dbname: Optional[str] = None,
    url: Optional[str] = None,
) -> tuple[str, str]:
    if url:
        parsed = make_url(url).set(drivername=_DRIVER[db_type])
        return parsed.render_as_string(hide_password=False), parsed.database

    if not all([host, port, user, password, dbname]):
        raise ValueError("provide either 'url', or all of host/port/user/password/dbname")
    return f"{_DRIVER[db_type]}://{user}:{password}@{host}:{port}/{dbname}", dbname


def _build_connection_blocking(db_type: DBType, sqlalchemy_url: str, dbname: str) -> Connection:
    engine = create_engine(sqlalchemy_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except Exception as exc:
        raise ConnectionUnreachableError(f"could not connect to {db_type} database '{dbname}'") from exc

    tables = _introspect(engine)
    logger.info("connected to %s database '%s' — %d table(s) found", db_type, dbname, len(tables))
    return Connection(engine=engine, db_type=db_type, dbname=dbname, tables=tables)


async def build_connection(db_type: DBType, sqlalchemy_url: str, dbname: str) -> Connection:
    """Connect and introspect. Off-thread because both steps are synchronous driver calls that can
    take seconds against a remote database — long enough to stall every other request if run
    directly on the event loop."""
    return await asyncio.to_thread(_build_connection_blocking, db_type, sqlalchemy_url, dbname)


# --- introspection ----------------------------------------------------------------------------

# schemas whose tables are driver/catalog internals, never user data — always skip these
_SYSTEM_SCHEMAS = {"information_schema", "pg_toast"}


def schemas_to_introspect(engine: Engine, inspector) -> list[Optional[str]]:
    # only postgres nests multiple schemas inside one connected database (mysql's "schema" IS the
    # database, so schema=None already reflects exactly the connected db — no iteration needed)
    if engine.dialect.name != "postgresql":
        return [None]
    schemas = [s for s in inspector.get_schema_names() if s not in _SYSTEM_SCHEMAS and not s.startswith("pg_")]
    return schemas or [None]


def qualify(schema: Optional[str], table: str) -> str:
    return f"{schema}.{table}" if schema else table


def _introspect(engine: Engine) -> dict[str, TableSchema]:
    inspector = inspect(engine)
    tables: dict[str, TableSchema] = {}

    for schema in schemas_to_introspect(engine, inspector):
        # bulk reflection: a handful of queries total, not 3-4 per table — matters a lot over a
        # high-latency link, where the old per-table loop meant dozens of extra round trips
        all_columns = inspector.get_multi_columns(schema=schema)
        all_pks = inspector.get_multi_pk_constraint(schema=schema)
        all_fks = inspector.get_multi_foreign_keys(schema=schema)

        for table_key, columns in all_columns.items():
            schema_name, table_name = table_key
            qualified_name = qualify(schema_name, table_name)
            pk_columns = set(all_pks.get(table_key, {}).get("constrained_columns") or [])

            cols = [ColumnSchema(name=c["name"], type=str(c["type"]), pk=c["name"] in pk_columns) for c in columns]
            fks = [
                ForeignKeySchema(
                    columns=fk["constrained_columns"],
                    referred_table=qualify(fk.get("referred_schema") or schema_name, fk["referred_table"]),
                    referred_columns=fk["referred_columns"],
                )
                for fk in all_fks.get(table_key, [])
            ]
            tables[qualified_name] = TableSchema(name=qualified_name, columns=cols, foreign_keys=fks)

    return tables


def render_schema_text(tables: dict[str, TableSchema], annotations: dict[tuple[str, str], str]) -> str:
    """Structural data (cached, expensive) + annotations (fresh, cheap) -> the text handed to the
    LLM. A pure function of its inputs so it's cheap to re-run on every request — annotations can
    be edited at any time and show up on the very next call, with no reconnect needed."""
    blocks = []
    for name in sorted(tables):
        table = tables[name]
        table_comment = annotations.get((name, ""))
        lines = [f"Table {name}:" + (f"  -- {table_comment}" if table_comment else "")]
        for col in table.columns:
            marker = " PK" if col.pk else ""
            col_comment = annotations.get((name, col.name))
            suffix = f"  -- {col_comment}" if col_comment else ""
            lines.append(f"  - {col.name} ({col.type}){marker}{suffix}")
        for fk in table.foreign_keys:
            constrained = ", ".join(fk.columns)
            referred = ", ".join(fk.referred_columns)
            lines.append(f"  - FK: {constrained} -> {fk.referred_table}({referred})")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


@dataclass(frozen=True)
class QueryResult:
    """One execution's output.

    Columns are carried separately from rows because an empty result still has a shape: a table
    with headers and no data renders, whereas an empty list of dicts is indistinguishable from
    having nothing to show.
    """

    columns: list[str]
    rows: list[dict]
    # we hit MAX_ROWS and there may be more behind it. False positive in exactly one case — a
    # result that happens to be MAX_ROWS rows long — which costs the user a truthful "capped at
    # 5000 rows" note and nothing else.
    truncated: bool

    @property
    def row_count(self) -> int:
        return len(self.rows)


def run_query(sql: str, connection: Connection) -> QueryResult:
    """The connection is passed in, never looked up — see DbContext for why."""
    try:
        with connection.engine.connect() as conn:
            if connection.db_type == "postgres":
                conn.exec_driver_sql(f"SET statement_timeout = {QUERY_TIMEOUT_MS}")
            else:
                conn.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME = {QUERY_TIMEOUT_MS}")
            result = conn.exec_driver_sql(sql)
            fetched = result.fetchmany(MAX_ROWS)
            columns = rows.unique_columns(result.keys())
    except Exception as exc:
        raise SQLExecutionError(str(exc)) from exc

    # outside the try: a type-conversion bug here is our fault, and reporting it as a failed query
    # would send the agent off fixing SQL that was already correct
    return QueryResult(
        columns=columns,
        rows=[{name: rows.to_jsonable(value) for name, value in zip(columns, row)} for row in fetched],
        truncated=len(fetched) >= MAX_ROWS,
    )
