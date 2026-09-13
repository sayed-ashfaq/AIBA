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


def column_embedding_text(table_name: str, column: models.Column) -> str:
    """The string embedded to represent ONE column, independent of its table's own
    vector — the counterpart to embedding_text() above.

    No LLM call: a table gets one, an LLM-written sentence, because there are few
    enough tables that a batched description call is cheap; a wide schema can have
    an order of magnitude more columns, and an LLM sentence per column would multiply
    build time and cost for a much narrower payoff (a column's name + type + sample
    values is already a strong retrieval signal on its own). Same tradeoff FalkorDB's
    QueryWeaver makes — its Column nodes embed a templated description too, never an
    LLM one.

    Qualified with the table name (`orders.status`, not bare `status`) so two same-
    named columns in different tables don't collide, and so the vector carries some
    of the same "which table" signal a bare column name would otherwise lack.
    """
    parts = [f"{table_name}.{column.name}", column.type]
    if column.pk:
        parts.append("primary key")
    if column.sample_values:
        label = "all values" if column.values_complete else "e.g."
        parts.append(f"{label} {', '.join(column.sample_values[:8])}")
    return " | ".join(parts)


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

        # Same invariant, one level down: column_index[i] must name the column whose
        # text was embedded into column_matrix row i. Built table-by-table in
        # table_names order (not that it matters for correctness — column_index is
        # its own source of truth — but it keeps the two matrices' row order
        # comparable in logs).
        column_index: list[tuple[str, str]] = [
            (name, col.name) for name in table_names for col in tables[name].columns
        ]
        column_matrix = embeddings.embed_documents(
            [column_embedding_text(name, col) for name in table_names for col in tables[name].columns]
        )

    assert matrix.shape[0] == len(table_names), "embedding rows must align 1:1 with table_names"
    assert column_matrix.shape[0] == len(column_index), "column embedding rows must align 1:1 with column_index"
    logger.info(
        "schema graph: %d tables, %d columns, %d FK edges, table embeddings %s, column embeddings %s",
        len(tables), len(column_index), g.number_of_edges(), matrix.shape, column_matrix.shape,
    )
    return models.SchemaGraph(
        graph=g, tables=tables, table_names=table_names, embeddings=matrix,
        column_index=column_index, column_embeddings=column_matrix,
    )
