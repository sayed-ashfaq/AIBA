"""LLM-written one-line descriptions of each table, for embedding.

The baseline embedding text ("store: store_id, manager_staff_id, address_id,
last_update") matches a question poorly — it's nowhere near "stores". A sentence
("store holds each physical rental location, its manager and its address")
embeds much closer, which is what fixes the Step 5 recall misses.

Runs once, at build time, a dozen tables per LLM call. A table the model omits,
or a batch whose call fails, is simply left out of the result — builder's
embedding_text falls back to the column list for those.
"""

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.subagents.sql_agent.schema_linking import models
from app.core.llm import get_llm
from app.core.logging import get_logger, log_duration

logger = get_logger(__name__)

_BATCH_SIZE = 12

_PROMPT = """For each table listed below, write ONE concise present-tense \
sentence describing what its rows represent and the key attributes it holds. \
Begin each sentence with the table name. Do not enumerate columns, do not add \
commentary. Return exactly one entry per input table, using the exact table \
name given (including any schema prefix)."""


class _Desc(BaseModel):
    name: str
    description: str


class _DescList(BaseModel):
    tables: list[_Desc]


def _table_summary(table: models.Table) -> str:
    """Compact one-line brief handed to the model for a single table."""
    cols = ", ".join(c.name for c in table.columns)
    refs = ", ".join(sorted({fk.to_table for fk in table.foreign_keys}))
    samples = "; ".join(
        f"{c.name}=[{', '.join(c.sample_values)}]" for c in table.columns if c.sample_values
    )
    parts = [f"{table.name} | columns: {cols}"]
    if refs:
        parts.append(f"references: {refs}")
    if samples:
        parts.append(f"sample values: {samples}")
    return " | ".join(parts)


def describe_tables(tables: dict[str, models.Table]) -> dict[str, str]:
    """{table name: one-line description} for every table the model returned one for."""
    names = sorted(tables)
    llm = get_llm("sql_agent").with_structured_output(_DescList)
    out: dict[str, str] = {}

    with log_duration(f"Describe {len(names)} tables"):
        for start in range(0, len(names), _BATCH_SIZE):
            batch = names[start : start + _BATCH_SIZE]
            body = "\n".join(_table_summary(tables[n]) for n in batch)
            try:
                result = llm.invoke([SystemMessage(content=_PROMPT), HumanMessage(content=body)])
            except Exception:
                logger.warning("description batch at %d failed", start, exc_info=True)
                continue
            for entry in result.tables:
                text = entry.description.strip()
                if entry.name in tables and text:
                    out[entry.name] = text

    missing = [n for n in names if n not in out]
    if missing:
        logger.info("no description for %d/%d tables: %s", len(missing), len(names), missing[:10])
    return out
