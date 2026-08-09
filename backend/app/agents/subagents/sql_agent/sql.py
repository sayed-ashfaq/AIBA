import re
import unicodedata

import sqlglot
import sqlparse

from app.agents.subagents.sql_agent import db
from app.core.exceptions import DestructiveSQLError, UnsafeSQLError

_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)

_FORBIDDEN_KEYWORDS = (
    "CREATE",
    "UPDATE",
    "DELETE",
    "DROP",
    "TRUNCATE",
    "INSERT",
    "ALTER",
    "EXEC",
    "EXECUTE",
)
_FORBIDDEN_RE = re.compile(r"\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE)
_ALLOWED_START_RE = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


def extract_sql(llm_output: str) -> str:
    fences = _FENCE_RE.findall(llm_output)
    if fences:
        return fences[-1].strip()
    return llm_output.strip()


def sanitize(sql: str) -> str:
    sql = unicodedata.normalize("NFKC", sql)
    sql = sql.replace("‘", "'").replace("’", "'")
    sql = sql.replace("“", '"').replace("”", '"')
    sql = sql.replace(" ", " ").replace("​", "")
    return sql.strip().rstrip(";").strip()


def format_sql(sql: str) -> str:
    return sqlparse.format(sql, reindent=True, keyword_case="upper")


def extract_statement(sql: str) -> str:
    statements = [s.strip() for s in sqlparse.split(sql) if s.strip()]
    if not statements:
        raise UnsafeSQLError("no SQL statement found in the model output")
    if len(statements) > 1:
        raise UnsafeSQLError("model output contained multiple SQL statements — only one is allowed")
    return statements[0].rstrip(";").strip()


def transpile(sql: str, dialect: str) -> str:
    try:
        result = sqlglot.transpile(sql, read=dialect, write=dialect, pretty=True)
    except Exception as exc:
        raise UnsafeSQLError(f"sqlglot could not parse the generated SQL: {exc}") from exc
    if not result:
        raise UnsafeSQLError("sqlglot produced no output for the generated SQL")
    return result[0]


def enforce_read_only(sql: str) -> None:
    if not _ALLOWED_START_RE.match(sql):
        raise DestructiveSQLError("only SELECT/WITH statements are allowed")
    match = _FORBIDDEN_RE.search(sql)
    if match:
        raise DestructiveSQLError(f"query contains a blocked write/destructive keyword: {match.group(1).upper()}")


def apply_row_cap(sql: str, dialect: str, cap: int = db.MAX_ROWS) -> str:
    """Cap the result size in the query itself.

    The generator used to be told to write its own LIMIT, which made the row budget something the
    model decided turn by turn — and capped every future chart at whatever number it happened to
    pick. The cap belongs here instead: deterministic, and applied whether or not the model
    cooperated.

    It has to be in the SQL rather than in fetchmany. psycopg2 buffers the entire result
    client-side before the first fetch, so a query matching ten million rows is ten million rows in
    this process no matter how few we go on to ask for.

    A smaller LIMIT the model wrote on purpose survives — "top 10" means ten.
    """
    try:
        expression = sqlglot.parse_one(sql, read=dialect)
    except Exception as exc:
        raise UnsafeSQLError(f"sqlglot could not parse the generated SQL: {exc}") from exc

    limit = expression.args.get("limit")
    if limit is not None:
        value = limit.expression
        if isinstance(value, sqlglot.exp.Literal) and value.is_int and int(value.name) <= cap:
            return sql

    # .limit() rewrites the outer LIMIT in place rather than wrapping the query in a subquery —
    # wrapping is where an inner ORDER BY stops being guaranteed to survive
    return expression.limit(cap).sql(dialect=dialect, pretty=True)


def clean_sql(llm_output: str, dialect: str) -> str:
    """Raw model output to ready-to-run SQL: extract it from the fence, normalise it, reformat it,
    make sure it's exactly one statement, transpile it for the target dialect, and cap its row
    count. The result is what OperationLoggingMiddleware logs when execute_sql runs it — logging
    each intermediate step here too would just show the same query five times over.
    """
    sql = extract_sql(llm_output)
    sql = sanitize(sql)
    sql = format_sql(sql)
    sql = extract_statement(sql)
    sql = transpile(sql, dialect)
    sql = sanitize(sql)
    enforce_read_only(sql)

    # capped last: enforce_read_only vets what the model actually wrote, and everything from here
    # on is SQL we generated ourselves
    return apply_row_cap(sql, dialect)


def clean_and_execute(llm_output: str, dialect: str, connection: db.Connection) -> tuple[str, db.QueryResult]:
    sql = clean_sql(llm_output, dialect)
    return sql, db.run_query(sql, connection)
