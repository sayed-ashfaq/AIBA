"""Tools exposed to the SQL subagent: get_schema (the active connection's schema text) and
execute_sql (runs cleaned, capped SQL against it via sql.clean_and_execute).

Thin wrappers over db.py/sql.py — the destructive-write guard and row cap already live there and
are reused unchanged.
"""

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from app.agents.shared.files import write_result
from app.agents.subagents.sql_agent import sql as sql_lib
from app.core.exceptions import NL2SQLError
from app.core.logging import get_logger

logger = get_logger(__name__)

_NO_CONNECTION = "No active database connection for this user. Tell the orchestrator you cannot proceed without one."

# rows inlined in the tool result so the model — and the orchestrator reading its final message —
# has concrete numbers to reason about without opening the full result file
SAMPLE_ROWS = 5


@tool
def get_schema(runtime: ToolRuntime) -> str:
    """Get the target database's schema: tables, columns, types, and foreign keys."""
    db_context = runtime.context.db_context
    if db_context is None:
        return _NO_CONNECTION
    return db_context.schema_text


@tool
def execute_sql(sql: str, runtime: ToolRuntime) -> str:
    """Run one read-only SQL statement (SELECT/WITH) against the target database.

    On failure, returns the error to read and fix before retrying. On success, returns a summary,
    a sample of the result rows, and the file path holding the full result set.
    """
    db_context = runtime.context.db_context
    if db_context is None:
        return _NO_CONNECTION

    try:
        cleaned_sql, result = sql_lib.clean_and_execute(sql, db_context.db_type, db_context.connection)
    except NL2SQLError as exc:
        logger.info("execute_sql rejected: %s", exc)
        return f"SQL error: {exc}"

    path = write_result(result.columns, result.rows, result.truncated)
    logger.info("execute_sql: %d row(s), truncated=%s, written to %s", result.row_count, result.truncated, path)

    sample = result.rows[:SAMPLE_ROWS]
    lines = [
        f"Ran: {cleaned_sql}",
        f"{result.row_count} row(s) returned"
        + (" (capped — more rows exist)" if result.truncated else "")
        + f". Columns: {', '.join(result.columns)}.",
        f"Full result set written to {path}.",
        f"Sample of {len(sample)} row(s):",
        *(str(row) for row in sample),
    ]
    return "\n".join(lines)
