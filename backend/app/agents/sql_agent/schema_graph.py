"""
EXPERIMENTAL — not wired into the real generation pipeline (sql_agent/agents.py still uses the
full flat schema_text from db.py for actual SQL generation). This is a standalone prototype of
graph-based schema linking:

    1. build_schema_graph(engine)   — tables -> nodes, FKs -> edges, once per connection.
    2. extract_entities(question)   — LLM pulls out the nouns the question is actually about.
    3. match_anchors(...)           — embedding similarity maps entities onto starting tables.
    4. find_join_path(...)          — graph traversal (shortest path / Steiner tree) over FK
                                       edges connects the anchors, surfacing bridge tables that
                                       embeddings alone would never find (no name similarity to
                                       the question, but structurally required for the join).
    5. build_focused_context(...)   — LLM renders {anchors + join path} into a minimal schema
                                       block: just the tables on the path, exact join conditions,
                                       and whatever extra columns the question's filters/aggregates
                                       need — not the whole database.

Embeddings only ever pick entry points; the graph guarantees the joins between them are real
FK-backed paths, not guesses.

render_graph_text(...) is the one exception to "not wired in": GET /connections/schema uses it
to show the built graph (nodes + FK edges) in the Streamlit UI, side by side with the plain
schema_text, purely for inspection/comparison — it doesn't touch SQL generation.
"""

from dataclasses import dataclass, field
from itertools import combinations
from typing import Optional

import networkx as nx
import numpy as np
from fastembed import TextEmbedding
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.agents.sql_agent import db
from app.core.llm import get_llm
from app.core.logging import get_logger, log_duration

logger = get_logger(__name__)

_EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_embedding_model: TextEmbedding | None = None


def _get_embedding_model() -> TextEmbedding:
    global _embedding_model
    if _embedding_model is None:
        with log_duration("Load embedding model"):
            _embedding_model = TextEmbedding(model_name=_EMBEDDING_MODEL_NAME)
    return _embedding_model


def _embed(texts: list[str]) -> np.ndarray:
    return np.array(list(_get_embedding_model().embed(texts)))


def _cosine_similarity(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    query_norm = query / (np.linalg.norm(query) + 1e-9)
    matrix_norm = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9)
    return matrix_norm @ query_norm


@dataclass
class TableInfo:
    name: str
    columns: list[dict]  # [{"name": str, "type": str, "pk": bool}, ...]


@dataclass
class FKEdge:
    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass
class SchemaGraph:
    graph: nx.MultiGraph
    tables: dict[str, TableInfo]
    table_names: list[str] = field(default_factory=list)
    embeddings: np.ndarray | None = None  # row i corresponds to table_names[i]


def build_schema_graph(engine: Engine) -> SchemaGraph:
    """Tables -> nodes, FK relationships -> edges. Call once per connection, cache the result."""
    inspector = inspect(engine)

    graph = nx.MultiGraph()
    tables: dict[str, TableInfo] = {}
    pending_fks: list[tuple[str, dict, Optional[str]]] = []  # (from_table, fk, schema)

    for schema in db.schemas_to_introspect(engine, inspector):
        all_columns = inspector.get_multi_columns(schema=schema)
        all_pks = inspector.get_multi_pk_constraint(schema=schema)
        all_fks = inspector.get_multi_foreign_keys(schema=schema)

        for table_key, columns in all_columns.items():
            schema_name, table_name = table_key
            qualified_name = db.qualify(schema_name, table_name)
            pk_columns = set(all_pks.get(table_key, {}).get("constrained_columns") or [])
            cols = [
                {"name": c["name"], "type": str(c["type"]), "pk": c["name"] in pk_columns} for c in columns
            ]
            tables[qualified_name] = TableInfo(name=qualified_name, columns=cols)
            graph.add_node(qualified_name)

        for table_key, fks in all_fks.items():
            from_table = db.qualify(table_key[0], table_key[1])
            for fk in fks:
                pending_fks.append((from_table, fk, schema))

    for from_table, fk, schema in pending_fks:
        to_table = db.qualify(fk.get("referred_schema") or schema, fk["referred_table"])
        if to_table not in tables:
            continue  # referenced table outside the introspected set
        for from_col, to_col in zip(fk["constrained_columns"], fk["referred_columns"]):
            graph.add_edge(
                from_table,
                to_table,
                from_table=from_table,
                from_column=from_col,
                to_table=to_table,
                to_column=to_col,
            )

    table_names = sorted(tables)
    descriptions = [f"{name}: {', '.join(c['name'] for c in tables[name].columns)}" for name in table_names]
    with log_duration("Embed schema tables"):
        embeddings = _embed(descriptions) if table_names else np.empty((0, 0))

    logger.info("schema graph built: %d tables, %d FK edges", len(tables), graph.number_of_edges())
    return SchemaGraph(graph=graph, tables=tables, table_names=table_names, embeddings=embeddings)


# planner statistics, not COUNT(*) — an exact count means a full scan per table, way too slow
# across a ~70-table schema. reltuples/table_rows are estimates (accurate after ANALYZE /
# InnoDB's periodic stats refresh), which is precise enough for sizing nodes in the graph view.
_ROW_COUNT_QUERY = {
    "postgresql": """
        SELECT n.nspname AS schema_name, c.relname AS table_name, c.reltuples::bigint AS estimate
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r' AND n.nspname NOT IN ('information_schema', 'pg_toast')
          AND n.nspname NOT LIKE 'pg\\_%%'
    """,
    # mysql's "schema" is the connected database itself — db.qualify(None, table) leaves it
    # unprefixed, so schema_name is left NULL here to match
    "mysql": """
        SELECT NULL AS schema_name, table_name, table_rows AS estimate
        FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
    """,
}


def get_row_counts(engine: Engine) -> dict[str, int]:
    """Approximate row count per table, keyed the same way as SchemaGraph.tables (qualified
    name via db.qualify) so the caller can zip it straight onto graph nodes."""
    query = _ROW_COUNT_QUERY.get(engine.dialect.name)
    if query is None:
        return {}
    try:
        with engine.connect() as conn:
            rows = conn.exec_driver_sql(query).fetchall()
    except Exception:
        logger.warning("could not fetch row count estimates", exc_info=True)
        return {}
    return {db.qualify(schema_name, table_name): max(int(estimate or 0), 0) for schema_name, table_name, estimate in rows}


def render_graph_text(schema: SchemaGraph) -> str:
    """The whole graph as text: every table (node) with its columns, plus every FK edge — the
    graph-shaped counterpart to db.py's flat schema_text, for side-by-side comparison in the UI.
    Unlike build_focused_context, this needs no question — it's the full structure, not a slice."""
    blocks = [
        "Table {}:\n{}".format(
            name,
            "\n".join(f"  - {c['name']} ({c['type']}){' PK' if c['pk'] else ''}" for c in schema.tables[name].columns),
        )
        for name in schema.table_names
    ]

    seen = set()
    edge_lines = []
    for _, _, data in schema.graph.edges(data=True):
        key = (data["from_table"], data["from_column"], data["to_table"], data["to_column"])
        if key in seen:
            continue
        seen.add(key)
        edge_lines.append(f"  - {data['from_table']}.{data['from_column']} -> {data['to_table']}.{data['to_column']}")

    header = f"Graph: {len(schema.table_names)} tables (nodes), {len(seen)} FK relationships (edges)"
    edges_block = "Edges (FK relationships):\n" + ("\n".join(edge_lines) if edge_lines else "  (none found)")

    return f"{header}\n\n" + "\n\n".join(blocks) + "\n\n" + edges_block


class _ExtractedEntities(BaseModel):
    entities: list[str]


_ENTITY_EXTRACTION_PROMPT = """Extract the key nouns/entities this question is asking about —
the things (people, objects, business concepts), not the actions or metrics. E.g. for "average
order value per supplier last month", extract ["order", "supplier"], not "average" or "last
month". Return 1-5 short entity terms."""


def extract_entities(question: str) -> list[str]:
    llm = get_llm("main_agent").with_structured_output(_ExtractedEntities)
    with log_duration("Extract entities"):
        result = llm.invoke([SystemMessage(content=_ENTITY_EXTRACTION_PROMPT), HumanMessage(content=question)])
    logger.info("extracted entities: %s", result.entities)
    return result.entities


def match_anchors(schema: SchemaGraph, entities: list[str], threshold: float = 0.4) -> list[str]:
    """Embedding similarity maps each extracted entity onto its closest table."""
    if not entities or not schema.table_names:
        return []

    anchors: list[str] = []
    query_vectors = _embed(entities)
    for entity, query_vector in zip(entities, query_vectors):
        similarities = _cosine_similarity(query_vector, schema.embeddings)
        best_idx = int(np.argmax(similarities))
        best_score = float(similarities[best_idx])
        if best_score >= threshold:
            table = schema.table_names[best_idx]
            if table not in anchors:
                anchors.append(table)
            logger.info("anchor: '%s' -> %s (score=%.2f)", entity, table, best_score)
        else:
            logger.info("anchor: '%s' -> no match above threshold (best=%.2f)", entity, best_score)
    return anchors


def find_join_path(schema: SchemaGraph, anchor_tables: list[str]) -> tuple[list[str], list[FKEdge]]:
    """Graph traversal connecting the anchors. Returns (tables on the path, FK edges used)."""
    anchors = [t for t in dict.fromkeys(anchor_tables) if t in schema.graph]
    if len(anchors) <= 1:
        return anchors, []

    if len(anchors) == 2:
        try:
            nodes = nx.shortest_path(schema.graph, anchors[0], anchors[1])
        except nx.NetworkXNoPath:
            logger.info("no path between %s and %s — disconnected in this schema", *anchors)
            return anchors, []
    else:
        # restrict to the connected component containing the anchors — steiner_tree's
        # approximation computes distances across the whole graph it's given, and chokes on
        # tables that are isolated/disconnected elsewhere in the schema (e.g. a migrations
        # table with no FKs at all), which have nothing to do with these anchors
        component = nx.node_connected_component(schema.graph, anchors[0])
        reachable = [a for a in anchors if a in component]
        unreachable = [a for a in anchors if a not in component]
        if unreachable:
            logger.info("anchors not reachable from %s, dropping: %s", anchors[0], unreachable)

        try:
            subgraph = schema.graph.subgraph(component)
            tree = nx.algorithms.approximation.steiner_tree(subgraph, reachable)
            nodes = list(tree.nodes)
        except (nx.NetworkXNoPath, nx.NodeNotFound, KeyError):
            logger.info("no connecting tree for anchors %s — falling back to pairwise paths", reachable)
            nodes = set(reachable)
            for a, b in combinations(reachable, 2):
                try:
                    nodes.update(nx.shortest_path(schema.graph, a, b))
                except nx.NetworkXNoPath:
                    continue
            nodes = list(nodes)

    edges: list[FKEdge] = []
    subgraph = schema.graph.subgraph(nodes)
    seen = set()
    for u, v, data in subgraph.edges(data=True):
        key = frozenset((u, v))
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            FKEdge(
                from_table=data["from_table"],
                from_column=data["from_column"],
                to_table=data["to_table"],
                to_column=data["to_column"],
            )
        )

    logger.info("join path: tables=%s edges=%s", nodes, [(e.from_table, e.to_table) for e in edges])
    return nodes, edges


FOCUSED_CONTEXT_PROMPT = """Given a user question and a set of anchor tables (matched by \
semantic similarity) plus their connecting join path (found via graph traversal over foreign \
keys), produce a minimal schema context for SQL generation.

Output only:
- The relevant tables with their columns/types
- The exact join conditions (table.column = table.column) for each edge in the join path
- Any columns needed for filtering/aggregation based on the question

Do not include tables outside the join path unless explicitly required by the question's \
filter/aggregation logic."""


def build_focused_context(
    question: str, schema: SchemaGraph, anchor_tables: list[str], join_path: list[str], edges: list[FKEdge]
) -> str:
    tables_block = "\n\n".join(
        f"Table {name}:\n"
        + "\n".join(f"  - {c['name']} ({c['type']}){' PK' if c['pk'] else ''}" for c in schema.tables[name].columns)
        for name in join_path
        if name in schema.tables
    )
    joins_block = "\n".join(
        f"  - {e.from_table}.{e.from_column} = {e.to_table}.{e.to_column}" for e in edges
    )

    context = (
        f"User question: {question}\n\n"
        f"Anchor tables: {', '.join(anchor_tables)}\n\n"
        f"Join path tables and columns:\n{tables_block}\n\n"
        f"Join path edges:\n{joins_block or '  (single table, no joins needed)'}"
    )

    with log_duration("Build focused context"):
        response = get_llm("sql_agent").invoke(
            [SystemMessage(content=FOCUSED_CONTEXT_PROMPT), HumanMessage(content=context)]
        )
    return response.content


def analyze_question(engine: Engine, question: str, schema: SchemaGraph | None = None) -> str:
    """End-to-end demo: build (or reuse) the graph, then run schema linking for one question."""
    schema = schema or build_schema_graph(engine)
    entities = extract_entities(question)
    anchors = match_anchors(schema, entities)
    join_path, edges = find_join_path(schema, anchors)
    return build_focused_context(question, schema, anchors, join_path, edges)
