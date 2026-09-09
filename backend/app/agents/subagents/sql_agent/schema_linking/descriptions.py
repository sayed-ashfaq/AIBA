"""LLM-written one-line descriptions of each table, for embedding.

The baseline embedding text ("store: store_id, manager_staff_id, address_id,
last_update") matches a question poorly — it's nowhere near "stores". A sentence
("store defines each store location, linking it to its manager and address")
embeds much closer, which is what fixes the Step 5 recall misses.

Runs once, at build time, a dozen tables per LLM call. The model is asked to
begin each line with the exact table name and we parse the lines back — NOT
structured output: gpt-oss on Groq ignores a forced tool call for a task whose
natural answer is prose, and the request 400s. A table whose line we can't parse,
or a batch whose call fails, is left out; builder's embedding_text falls back to
the column list for those.
"""

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.subagents.sql_agent.schema_linking import models
from app.core.llm import get_llm
from app.core.logging import get_logger, log_duration

logger = get_logger(__name__)

_BATCH_SIZE = 12

_PROMPT = """You are given database tables, one per line, each as:
    <table_name> | columns: ... | references: ... | sample values: ...

For each table write ONE present-tense sentence that will be EMBEDDED and matched
against user questions — so write it as a retrieval target, not a data-model note:
- say what one row represents and the kind of business question it answers
- name the key things someone would filter or group by (status, type,
  location/terminal, date, category), using the words a user would actually say
- fold in common synonyms for the entity when they fit (guest / VIP / passenger /
  member; shipment / order / delivery; staff / employee)

Do not enumerate every column. Output one line per table, exactly:
    <table_name>: <sentence>

Use the table_name exactly as given (keep any schema prefix). No blank lines,
headings, or commentary."""


def _table_summary(table: models.Table) -> str:
    """Compact one-line brief handed to the model for a single table."""
    cols = ", ".join(c.name for c in table.columns)
    refs = ", ".join(sorted({fk.to_table for fk in table.foreign_keys}))
    # a flavour of the values is enough to write a one-liner, and to let the model
    # spot which columns are the filterable dimensions — a fully enumerated column
    # can carry ~30 values, which this prompt doesn't need
    samples = "; ".join(
        f"{c.name}=[{', '.join(c.sample_values[:8])}]" for c in table.columns if c.sample_values
    )
    parts = [f"{table.name} | columns: {cols}"]
    if refs:
        parts.append(f"references: {refs}")
    if samples:
        parts.append(f"sample values: {samples}")
    return " | ".join(parts)


def _parse(text: str, batch: set[str]) -> dict[str, str]:
    """Pull `name: sentence` (or `name sentence`) lines out of the model's reply,
    keeping only names that were in this batch."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.lstrip("-*• \t").strip()
        if not line:
            continue
        # tolerate "name: sentence" and "name sentence"
        head, sep, rest = line.partition(":")
        if not sep or head.strip() not in batch:
            head, _, rest = line.partition(" ")
        name = head.strip()
        desc = rest.strip()
        if name in batch and desc:
            out[name] = desc
    return out


def describe_tables(tables: dict[str, models.Table]) -> dict[str, str]:
    """{table name: one-line description} for every table we could parse a line for."""
    names = sorted(tables)
    llm = get_llm("sql_agent")
    out: dict[str, str] = {}

    with log_duration(f"Describe {len(names)} tables"):
        for start in range(0, len(names), _BATCH_SIZE):
            batch = names[start : start + _BATCH_SIZE]
            body = "\n".join(_table_summary(tables[n]) for n in batch)
            try:
                reply = llm.invoke([SystemMessage(content=_PROMPT), HumanMessage(content=body)])
            except Exception:
                logger.warning("description batch at %d failed", start, exc_info=True)
                continue
            parsed = _parse(reply.content, set(batch))
            if not parsed:
                logger.warning("description batch at %d: nothing parsed from reply", start)
            out.update(parsed)

    missing = [n for n in names if n not in out]
    if missing:
        logger.info("no description for %d/%d tables: %s", len(missing), len(names), missing[:10])
    return out
