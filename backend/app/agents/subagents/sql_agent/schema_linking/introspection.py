"""Live database -> {qualified_name: models.Table}.

Structure comes from SQLAlchemy's inspector using *bulk* reflection
(get_multi_* — a handful of catalog queries total, not 3-4 per table), the same
approach db.py's _introspect uses. On top of that, an optional pass samples a
few real values for low-cardinality text columns, so later stages can show the
SQL generator the actual literals to filter on ("rating = 'PG-13'").

No graph, no embeddings, no LLM. Reuses db.qualify / db.schemas_to_introspect so
the qualified names line up exactly with the flat-schema path.
"""

import re
from contextlib import nullcontext
from dataclasses import replace

from sqlalchemy import String, inspect, text
from sqlalchemy.engine import Engine

from app.agents.subagents.sql_agent import db
from app.agents.subagents.sql_agent.schema_linking import models
from app.core.logging import get_logger, log_duration

logger = get_logger(__name__)

# A sampled column is only useful if its values behave like a small enumerated
# set. These are the defaults; callers can override per connection if needed.
_SAMPLE_SIZE = 3          # how many example values to keep
_MAX_DISTINCT = 20        # more distinct values than this -> treat as high-cardinality, keep none
_STATEMENT_TIMEOUT_MS = 5000  # a sampling query must never hold things up

# A value longer than this is prose, a blob, an email, or a hash — never a
# category label. If ANY sampled value exceeds it we drop the whole column.
_MAX_VALUE_LEN = 40

# Never sample a column whose name contains one of these — secrets, contact
# details, personal identifiers and infra addresses must not reach a prompt or a
# log, regardless of cardinality.
_SENSITIVE_NAME_HINTS = (
    "password", "passwd", "secret", "token", "apikey", "api_key",
    "hash", "salt", "email", "e_mail", "mail", "phone", "mobile", "fax", "ssn",
    "creditcard", "credit_card", "cardnumber", "card_number", "url", "website",
    "firstname", "first_name", "lastname", "last_name", "fullname", "full_name",
    "username", "user_name", "smtp", "passport", "national_id", "nationalid",
    "nationality", "iqama", "dob", "birth", "latitude", "longitude",
    "ipaddr", "ip_addr", "ipaddress", "ip_address",
)

# A name check can't catch PII in a free-text or oddly-named column (a description
# that happens to hold an email, a "gatewayTerminal" that holds an IP). As a
# backstop, if any sampled value itself matches one of these, drop the column.
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+"),        # email address
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),     # IPv4 address
)


def introspect(
    engine: Engine,
    *,
    sample: bool = True,
    sample_size: int = _SAMPLE_SIZE,
    max_distinct: int = _MAX_DISTINCT,
) -> dict[str, models.Table]:
    """Reflect every user table into a models.Table. `sample=False` skips all
    value sampling (no extra queries) — useful while iterating."""
    inspector = inspect(engine)
    tables: dict[str, models.Table] = {}
    # (from_table, fk_dict, schema) collected first, resolved after every table
    # exists — an FK can point at a table we haven't built yet.
    pending_fks: list[tuple[str, dict, str | None]] = []

    # One connection held open for the whole sampling pass; nullcontext when we
    # are not sampling, so `conn` is simply None and no connection is opened.
    conn_cm = engine.connect() if sample else nullcontext()
    with conn_cm as conn:
        if conn is not None and engine.dialect.name == "postgresql":
            conn.exec_driver_sql(f"SET statement_timeout = {_STATEMENT_TIMEOUT_MS}")

        for schema in db.schemas_to_introspect(engine, inspector):
            all_columns = inspector.get_multi_columns(schema=schema)
            all_pks = inspector.get_multi_pk_constraint(schema=schema)
            all_fks = inspector.get_multi_foreign_keys(schema=schema)

            for (schema_name, table_name), columns in all_columns.items():
                key = (schema_name, table_name)
                qname = db.qualify(schema_name, table_name)
                pk_cols = set(all_pks.get(key, {}).get("constrained_columns") or [])
                fk_cols = {c for fk in all_fks.get(key, []) for c in fk["constrained_columns"]}

                cols = []
                for c in columns:
                    samples: tuple[str, ...] = ()
                    # Skip PKs (unique by definition) and FK columns (they hold ids,
                    # not a meaningful value set) before spending a query on them.
                    if sample and c["name"] not in pk_cols and c["name"] not in fk_cols:
                        samples = _sample_values(conn, engine, qname, c, sample_size, max_distinct)
                    cols.append(
                        models.Column(
                            name=c["name"],
                            type=str(c["type"]),
                            pk=c["name"] in pk_cols,
                            nullable=bool(c.get("nullable", True)),
                            sample_values=samples,
                        )
                    )
                tables[qname] = models.Table(name=qname, columns=cols)

            for (schema_name, table_name), fks in all_fks.items():
                from_table = db.qualify(schema_name, table_name)
                for fk in fks:
                    pending_fks.append((from_table, fk, schema))

    for from_table, fk, schema in pending_fks:
        to_table = db.qualify(fk.get("referred_schema") or schema, fk["referred_table"])
        if to_table not in tables:
            continue  # referenced table outside the introspected set — skip the edge
        for from_col, to_col in zip(fk["constrained_columns"], fk["referred_columns"]):
            tables[from_table].foreign_keys.append(
                models.FKEdge(
                    from_table=from_table,
                    from_column=from_col,
                    to_table=to_table,
                    to_column=to_col,
                )
            )

    edge_count = sum(len(t.foreign_keys) for t in tables.values())
    sampled = sum(1 for t in tables.values() for c in t.columns if c.sample_values)
    logger.info(
        "introspected %d tables, %d FK edges, %d columns sampled", len(tables), edge_count, sampled
    )
    return tables


def _sample_values(conn, engine: Engine, qualified_table: str, coldict: dict, size: int, max_distinct: int) -> tuple[str, ...]:
    """A few example values for one column, or () if it isn't a small label set."""
    col_name = coldict["name"]
    lname = col_name.lower()
    if any(hint in lname for hint in _SENSITIVE_NAME_HINTS) or lname.endswith("id"):
        return ()  # secret/contact column, or an id stored as text — never a useful hint

    col_type = coldict["type"]

    # A Postgres ENUM already carries its full label set from reflection — no query.
    enums = getattr(col_type, "enums", None)
    if enums:
        return tuple(str(v).strip() for v in enums[:12])

    # Only text columns are worth sampling. `Text` subclasses `String`, so this
    # covers VARCHAR / CHAR / TEXT. Numbers, dates, uuids, booleans, and
    # unresolved pg DOMAINs are skipped.
    if not isinstance(col_type, String):
        return ()

    qt = _quote_ident(engine, qualified_table)
    qc = _quote_ident(engine, col_name)
    # DISTINCT + LIMIT max_distinct+1: if we get more than max_distinct rows back
    # the column is high-cardinality (a name, a description) and the samples would
    # be noise, so we drop them. The +1 tells "exactly at the cap" from "over it".
    stmt = text(f"SELECT DISTINCT {qc} AS v FROM {qt} WHERE {qc} IS NOT NULL LIMIT :lim")
    try:
        rows = conn.execute(stmt, {"lim": max_distinct + 1}).fetchall()
    except Exception:
        logger.warning("value sampling failed for %s.%s", qualified_table, col_name, exc_info=True)
        return ()

    if len(rows) > max_distinct:
        return ()

    values = [str(r.v).strip() for r in rows]
    values = [v for v in values if v]  # drop empty / whitespace-only
    # One long value means the column holds free text, not labels — drop it whole
    # rather than keep a truncated-looking subset.
    if not values or any(len(v) > _MAX_VALUE_LEN for v in values):
        return ()
    # value-level PII backstop — never log the value, only the column it was in
    if any(p.search(v) for v in values for p in _SENSITIVE_VALUE_PATTERNS):
        logger.info("skipped sampling %s.%s — a value matched a sensitive pattern", qualified_table, col_name)
        return ()
    # dedupe after stripping ('S ' and 'S' collapse), preserve encounter order
    return tuple(list(dict.fromkeys(values))[:size])


def _quote_ident(engine: Engine, dotted: str) -> str:
    """'public.film' -> '"public"."film"', quoting each part with the dialect's
    rules. Assumes no literal dots inside a schema/table name (same assumption
    db.qualify makes)."""
    preparer = engine.dialect.identifier_preparer
    return ".".join(preparer.quote(part) for part in dotted.split("."))
