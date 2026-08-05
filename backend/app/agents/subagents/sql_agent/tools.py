"""Tools exposed to the SQL subagent: get_schema (the active connection's schema text),
sql_generator (one dedicated LLM call that turns a schema + task into validated SQL), and
execute_sql (runs cleaned, capped SQL against it via sql.clean_and_execute).

SQL generation is split out from the subagent's own ReAct reasoning into its own tool/call because
asking one model turn to both decide what to do next *and* get SQL syntax right, in one pass,
produced worse SQL on anything non-trivial than a single call dedicated to just writing it. The
subagent's job becomes: gather schema, decide what's relevant, describe the task clearly (breaking
complex requests into explicit steps), and drive generate -> execute -> retry.

Thin wrappers over db.py/sql.py — the destructive-write guard and row cap already live there and
are reused unchanged.
"""

from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from app.agents.shared.files import write_result
from app.agents.subagents.sql_agent import sql as sql_lib
from app.core.exceptions import NL2SQLError
from app.core.llm import get_llm
from app.core.logging import get_logger
from app.agents.subagents.sql_agent.prompt import SQL_GENERATION_PROMPT

logger = get_logger(__name__)

_NO_CONNECTION = "No active database connection for this user. Tell the orchestrator you cannot proceed without one."

# results at or under this many rows are inlined in full rather than sampled. Most business "top N"
# questions return well under this, and inlining everything is what stops a fixed-size sample from
# silently cutting off exactly at the boundary of what was asked for — e.g. a "top 5 categories"
# query that also carries one extra summary row ends up 6 rows long, and a 5-row sample would show
# 4 real categories plus the summary row, never the 5th category at all
FULL_INLINE_THRESHOLD = 20

# how many rows to inline when the result is bigger than that — enough for the model to work with
# without either tool response ballooning
SAMPLE_ROWS = 10



@tool
def get_schema(runtime: ToolRuntime) -> str:
    """Get the target database's schema: tables, columns, types, and foreign keys."""
    db_context = runtime.context.db_context
    if db_context is None:
        return _NO_CONNECTION
    return db_context.schema_text


@tool
def sql_generator(schema_context: str, task: str, runtime: ToolRuntime) -> str:
    """Generate one validated SQL statement from a schema and a task description.

    schema_context: the tables/columns the query needs — the full schema from get_schema is fine
    for now. task: a clear, specific description of what the query should do. For anything beyond
    a single-table lookup, spell out the steps (which tables, how they join, filters, aggregation)
    rather than restating the raw request — the more explicit the task, the more reliable the SQL.
    When retrying after a failed attempt, include the previous error in the task so it isn't repeated.

    Returns cleaned, dialect-correct SQL ready for execute_sql, or a validation error to fix and
    retry with. Does not run the query — call execute_sql with the result to do that.
    """
    db_context = runtime.context.db_context
    if db_context is None:
        return _NO_CONNECTION

    system_prompt = SQL_GENERATION_PROMPT.format(dialect=db_context.db_type)
    content = f"Schema:\n{schema_context}\n\nTask:\n{task}"
    response = get_llm("sql_agent").invoke([SystemMessage(content=system_prompt), HumanMessage(content=content)])

    try:
        return sql_lib.clean_sql(response.content, db_context.db_type)
    except NL2SQLError as exc:
        logger.info("sql_generator produced invalid SQL: %s", exc)
        return f"Generation error: {exc}"


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

    path = write_result(result.columns, result.rows, result.truncated, cleaned_sql)
    logger.info("execute_sql: %d row(s), truncated=%s, written to %s", result.row_count, result.truncated, path)

    is_full = result.row_count <= FULL_INLINE_THRESHOLD
    sample = result.rows if is_full else result.rows[:SAMPLE_ROWS]

    lines = [
        f"Ran: {cleaned_sql}",
        f"{result.row_count} row(s) returned"
        + (" (capped — more rows exist)" if result.truncated else "")
        + f". Columns: {', '.join(result.columns)}.",
        f"Full result set written to {path}.",
        f"All {len(sample)} row(s):"
        if is_full
        else f"First {len(sample)} of {result.row_count} row(s) — this is a partial sample, read {path} for the rest before answering:",
        *(str(row) for row in sample),
    ]
    return "\n".join(lines)
