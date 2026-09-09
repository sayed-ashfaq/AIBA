"""Tools exposed to the SQL subagent: get_schema (the active connection's schema text),
sql_generator (one dedicated LLM call that turns a schema + task into validated SQL), and
execute_sql (runs cleaned, capped SQL against it, with an internal targeted-repair loop).

SQL generation is split out from the subagent's own ReAct reasoning into its own tool/call because
asking one model turn to both decide what to do next *and* get SQL syntax right, in one pass,
produced worse SQL on anything non-trivial than a single call dedicated to just writing it. The
subagent's job becomes: gather schema, decide what's relevant, describe the task clearly (breaking
complex requests into explicit steps), and drive generate -> execute -> retry.

execute_sql repairs its own mechanical failures: on a DB or validation error it calls _fix_sql
(one focused LLM call: failing SQL + verbatim error + schema + task + earlier failed fixes) for
the SMALLEST change that clears that error, then re-runs, up to _MAX_SQL_FIXES times. This is a
deterministic inner loop, invisible to the ReAct model and to ToolCallLimitMiddleware — the model
only sees a result or, once repair is exhausted, an error to rethink with sql_generator. Semantic
mistakes (wrong table, missing join) can't be patched this way and still surface to the model.

A fix that changes which tables the query reads from is rejected outright (the model will happily
"fix" `FROM missing_table` by substituting a real one and returning unrelated rows) — the guard
compares the table set before and after and, on any change, drops the fix and surfaces the
original error instead.

Thin wrappers over db.py/sql.py — the destructive-write guard and row cap already live there and
are reused unchanged.
"""

import sqlglot
from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from app.agents.shared.files import write_result
from app.agents.subagents.sql_agent import db as db_lib
from app.agents.subagents.sql_agent import sql as sql_lib
from app.core.exceptions import NL2SQLError
from app.core.llm import get_llm
from app.core.logging import get_logger
from app.agents.subagents.sql_agent.prompt import SQL_FIX_PROMPT, SQL_GENERATION_PROMPT

logger = get_logger(__name__)

# how many targeted-repair passes execute_sql makes on a failing query before it gives up and
# hands the error back to the ReAct model
_MAX_SQL_FIXES = 3

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

# a real schema is columns + types + FK lines; a schema_context shorter than this is a stub — the
# agent passed a table name or a paraphrase instead of the get_schema output. Fall back to the full
# schema so sql_generator isn't writing blind (this is the #1 cause of invented column names).
_MIN_SCHEMA_CONTEXT = 120



def resolve_schema(db_context, task: str) -> str:
    """The schema text for a task. Plain mode: the full flat schema. Graph mode:
    schema_linking's question-relevant slice, falling back to the full schema if
    linking produced nothing."""
    if getattr(db_context, "schema_mode", "plain") != "graph" or db_context.schema_graph is None:
        return db_context.schema_text

    from app.agents.subagents.sql_agent import schema_linking

    try:
        focused = schema_linking.link(task, db_context.schema_graph)
        sliced = schema_linking.render_focused(focused, db_context.schema_graph)
    except Exception:
        # graph mode is experimental — never let a linking failure break a query the flat
        # schema could have answered
        logger.warning("graph schema linking errored for %r — using full schema", task, exc_info=True)
        return db_context.schema_text

    if sliced:
        logger.info(
            "graph schema: %d tables for task %r (vs %d in full schema)",
            len(focused.path_tables), task, len(db_context.schema_graph.tables),
        )
        return sliced
    logger.info("graph schema: linking found nothing for %r — using full schema", task)
    return db_context.schema_text


@tool
def get_schema(task: str, runtime: ToolRuntime) -> str:
    """Get the database schema you need to write a query.

    task: a brief description of the data you need (e.g. "monthly revenue per product
    category"). In graph mode this selects the relevant tables; otherwise it's ignored
    and the full schema is returned.
    """
    db_context = runtime.context.db_context
    if db_context is None:
        return _NO_CONNECTION
    return resolve_schema(db_context, task)


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

    if len(schema_context.strip()) < _MIN_SCHEMA_CONTEXT:
        logger.warning(
            "sql_generator got a %d-char schema_context — falling back to the full schema",
            len(schema_context.strip()),
        )
        schema_context = db_context.schema_text

    system_prompt = SQL_GENERATION_PROMPT.format(dialect=db_context.db_type)
    content = f"Schema:\n{schema_context}\n\nTask:\n{task}"
    response = get_llm("sql_agent").invoke([SystemMessage(content=system_prompt), HumanMessage(content=content)])

    try:
        return sql_lib.clean_sql(response.content, db_context.db_type)
    except NL2SQLError as exc:
        logger.info("sql_generator produced invalid SQL: %s", exc)
        return f"Generation error: {exc}"


def _fix_sql(failing_sql: str, db_error: str, db_context, task: str, prior: list[tuple[str, str]]) -> str:
    """One focused-repair LLM call. Returns raw (fenced) SQL for the caller to clean and re-run.

    `prior` is every (sql, error) pair tried so far this round, newest last — the entries before
    the last one are shown to the model so it doesn't re-make a fix that already failed.
    """
    earlier = (
        "\n".join(f"- tried: {s}\n  still failed: {e}" for s, e in prior[:-1])
        if len(prior) > 1
        else "(none)"
    )
    system_prompt = SQL_FIX_PROMPT.format(dialect=db_context.db_type)
    content = (
        f"Schema:\n{db_context.schema_text}\n\n"
        f"Data request:\n{task or '(not provided)'}\n\n"
        f"Failing SQL:\n{failing_sql}\n\n"
        f"Database error:\n{db_error}\n\n"
        f"Earlier failed fixes this round:\n{earlier}"
    )
    response = get_llm("sql_agent").invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=content)]
    )
    return response.content


def _table_names(sql: str, dialect: str) -> set[str]:
    """Lower-cased physical table names a query reads from, or an empty set if it won't parse."""
    try:
        parsed = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return set()
    return {t.name.lower() for t in parsed.find_all(sqlglot.exp.Table) if t.name}


def _tables_changed(before: str, after: str, dialect: str) -> bool:
    """True when `after` reads from a different set of tables than `before` (both parseable).
    A repair is only ever a local edit; a changed FROM/JOIN target means the fixer rewrote the
    query — reject it and surface the original error instead."""
    b, a = _table_names(before, dialect), _table_names(after, dialect)
    return bool(b) and bool(a) and a != b


def _execute_with_repair(raw_sql: str, db_context, task: str):
    """clean_sql -> run_query, and on an NL2SQLError run up to _MAX_SQL_FIXES targeted repairs
    before giving up. Returns (cleaned_sql, QueryResult, n_repairs); raises the last NL2SQLError
    if every attempt still fails or a repair is rejected for changing the table set.
    """
    dialect = db_context.db_type
    attempts: list[tuple[str, str]] = []  # (sql text tried, error) — newest last
    candidate = raw_sql
    last_exc: NL2SQLError | None = None

    for round_no in range(_MAX_SQL_FIXES + 1):
        failing: str
        try:
            cleaned = sql_lib.clean_sql(candidate, dialect)
        except NL2SQLError as exc:
            last_exc, failing = exc, candidate
        else:
            try:
                result = db_lib.run_query(cleaned, db_context.connection)
                return cleaned, result, len(attempts)
            except NL2SQLError as exc:
                last_exc, failing = exc, cleaned

        attempts.append((failing, str(last_exc)))
        if round_no == _MAX_SQL_FIXES:
            break
        logger.info("execute_sql error (repair %d/%d): %s", round_no + 1, _MAX_SQL_FIXES, last_exc)

        fixed = _fix_sql(failing, str(last_exc), db_context, task, attempts)
        if _tables_changed(failing, fixed, dialect):
            logger.info(
                "execute_sql repair changed the table set (%s -> %s) — rejecting, surfacing original error",
                sorted(_table_names(failing, dialect)), sorted(_table_names(fixed, dialect)),
            )
            break
        candidate = fixed

    assert last_exc is not None
    raise last_exc


@tool
def execute_sql(sql: str, runtime: ToolRuntime, task: str = "") -> str:
    """Run one read-only SQL statement (SELECT/WITH) against the target database.

    task: the data request this query answers — pass the same text you gave sql_generator. It is
    used only to steer the automatic repair step if the query errors.

    A query that fails on a mechanical error (bad identifier, missing cast, GROUP BY omission) is
    repaired and re-run automatically. If it still errors after that, the error is returned for
    you to rethink and regenerate. On success, returns a summary, a sample of the result rows,
    and the file path holding the full result set.
    """
    db_context = runtime.context.db_context
    if db_context is None:
        return _NO_CONNECTION

    try:
        cleaned_sql, result, n_repairs = _execute_with_repair(sql, db_context, task)
    except NL2SQLError as exc:
        logger.info("execute_sql unresolved after %d repair attempt(s): %s", _MAX_SQL_FIXES, exc)
        return f"SQL error (still failing after {_MAX_SQL_FIXES} automatic fix attempts): {exc}"

    path = write_result(result.columns, result.rows, result.truncated, cleaned_sql)
    logger.info(
        "execute_sql: %d row(s), truncated=%s, repairs=%d, written to %s",
        result.row_count, result.truncated, n_repairs, path,
    )

    is_full = result.row_count <= FULL_INLINE_THRESHOLD
    sample = result.rows if is_full else result.rows[:SAMPLE_ROWS]

    lines = [
        *([f"(auto-corrected {n_repairs} SQL error(s) before this ran)"] if n_repairs else []),
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
