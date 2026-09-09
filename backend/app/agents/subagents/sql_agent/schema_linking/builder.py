"""Build the per-connection SchemaGraph: introspect -> graph -> embed -> assemble.

Expensive — catalog queries, value sampling, an embedding per table, and the
embedding model's first-call load — so a caller builds it once and caches it for
the connection's lifetime (ActiveConnection.schema_graph). This module only builds.
"""

from sqlalchemy.engine import Engine

from app.agents.subagents.sql_agent.schema_linking import (
    descriptions,
    embeddings,
    graph,
    introspection,
    models,
)
from app.core.logging import get_logger, log_duration

logger = get_logger(__name__)


# budget for the column section of an embedding doc — bge-small tops out at 512
# tokens, and dense signal matches better than a long noisy list
_EMBED_COLS_MAX_CHARS = 800
_EMBED_VALS_PER_COL = 8


def _columns_for_embedding(table: models.Table) -> str:
    """`name (val, val, ...)` per column — the sample values are what lets a question
    like "Jeddah terminal" match the table whose `terminal` column holds 'Jeddah'.
    Values come from introspection, which already drops PII/secret columns. Capped
    so a wide table doesn't produce an oversized, diluted vector."""
    parts: list[str] = []
    used = 0
    for c in table.columns:
        piece = c.name
        if c.sample_values:
            piece += " (" + ", ".join(c.sample_values[:_EMBED_VALS_PER_COL]) + ")"
        if parts and used + len(piece) + 2 > _EMBED_COLS_MAX_CHARS:
            break
        parts.append(piece)
        used += len(piece) + 2
    return ", ".join(parts)


def embedding_text(table: models.Table) -> str:
    """The string embedded to represent a table for anchor matching.

    With an LLM description (the normal case): the sentence plus the columns and
    their sample values, so "what is this table", column-term, and value-term
    questions ("cancelled flights", "Jeddah terminal") all have something to match.
    Without one (model omitted it / describe=False): fall back to name + columns.
    """
    cols = _columns_for_embedding(table)
    if table.description:
        return f"{table.description}\nColumns: {cols}"
    return f"{table.name}: {cols}"


def build_schema_graph(
    engine: Engine, *, sample: bool = True, describe: bool = True
) -> models.SchemaGraph:
    with log_duration("Build schema graph"):
        tables = introspection.introspect(engine, sample=sample)

        if describe:
            for name, text in descriptions.describe_tables(tables).items():
                tables[name].description = text

        g = graph.build_graph(tables)

        # INVARIANT: table_names order == embedding row order. linking maps a
        # similarity-score row index straight back through table_names[i], so
        # these two lists must be built from the same sequence.
        table_names = sorted(tables)
        matrix = embeddings.embed_documents([embedding_text(tables[n]) for n in table_names])

    assert matrix.shape[0] == len(table_names), "embedding rows must align 1:1 with table_names"
    logger.info(
        "schema graph: %d tables, %d FK edges, embeddings %s",
        len(tables), g.number_of_edges(), matrix.shape,
    )
    return models.SchemaGraph(
        graph=g, tables=tables, table_names=table_names, embeddings=matrix
    )
