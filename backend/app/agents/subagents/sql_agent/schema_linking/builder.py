"""Build the per-connection SchemaGraph: introspect -> graph -> embed -> assemble.

Expensive — catalog queries, value sampling, an embedding per table, and the
embedding model's first-call load — so a caller builds it once and caches it for
the connection's lifetime (ActiveConnection.schema_graph). This module only builds.
"""

from sqlalchemy.engine import Engine

from app.agents.subagents.sql_agent.schema_linking import embeddings, graph, introspection, models
from app.core.logging import get_logger, log_duration

logger = get_logger(__name__)


def embedding_text(table: models.Table) -> str:
    """The string embedded to represent a table for anchor matching.

    Step 6 baseline: qualified name + column names. Step 7 replaces this with an
    LLM-written one-line description (and folds in sample values); this is the
    single place that changes.
    """
    return f"{table.name}: {', '.join(c.name for c in table.columns)}"


def build_schema_graph(engine: Engine, *, sample: bool = True) -> models.SchemaGraph:
    with log_duration("Build schema graph"):
        tables = introspection.introspect(engine, sample=sample)
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
