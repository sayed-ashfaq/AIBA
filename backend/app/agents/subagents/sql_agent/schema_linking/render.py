"""Structured schema data -> the text an LLM reads. Deterministic; no LLM call.

render_focused()    — the per-question slice (linking's FocusedSchema): the
                      chosen tables with columns + sample values, then the exact
                      join conditions. This is what goes to sql_generator.
render_graph_text() — the whole graph as text (every table + every FK edge), the
                      graph-shaped counterpart to db.render_schema_text, for the
                      schema-inspection UI.

Column formatting mirrors db.render_schema_text so the model sees the same shape
whichever schema mode is active.
"""

from app.agents.subagents.sql_agent.schema_linking import models


def _q(name: str) -> str:
    """An identifier as it must be written in SQL: bare if it's a plain lower-case
    name, double-quoted otherwise (Postgres folds unquoted names to lower case, so
    `scheduledDeparture` only works as `"scheduledDeparture"`)."""
    if name.islower() and name.replace("_", "").isalnum():
        return name
    return f'"{name}"'


def _column_line(col: models.Column) -> str:
    pk = " PK" if col.pk else ""
    hint = f"  -- e.g. {', '.join(col.sample_values)}" if col.sample_values else ""
    return f"  - {_q(col.name)} ({col.type}){pk}{hint}"


def _table_block(table: models.Table, *, include_fks: bool) -> str:
    head = f"Table {table.name}:" + (f"  -- {table.description}" if table.description else "")
    lines = [head, *(_column_line(c) for c in table.columns)]
    if include_fks:
        lines += [
            f"  - FK: {_q(fk.from_column)} -> {fk.to_table}({_q(fk.to_column)})"
            for fk in table.foreign_keys
        ]
    return "\n".join(lines)


def render_focused(focused: models.FocusedSchema, sg: models.SchemaGraph) -> str:
    """The linked slice as text. Empty string when linking found nothing — the
    caller falls back to the full schema."""
    if not focused.path_tables:
        return ""

    blocks = [
        _table_block(sg.tables[name], include_fks=False)
        for name in focused.path_tables
        if name in sg.tables
    ]
    if focused.edges:
        joins = "\n".join(
            f"  {e.from_table}.{_q(e.from_column)} = {e.to_table}.{_q(e.to_column)}"
            for e in focused.edges
        )
    else:
        joins = "  (single table — no joins needed)"

    return (
        f"-- schema slice for: {focused.question}\n"
        f"-- {len(blocks)} table(s), connected by foreign keys\n\n"
        + "\n\n".join(blocks)
        + "\n\nJoins:\n"
        + joins
    )


def render_graph_text(sg: models.SchemaGraph) -> str:
    """Every table (with columns + FK lines) plus a consolidated edge list — the
    graph-shaped counterpart to the flat db.render_schema_text."""
    blocks = [
        _table_block(sg.tables[name], include_fks=True)
        for name in sg.table_names
        if name in sg.tables
    ]

    seen: set[tuple[str, str, str, str]] = set()
    edge_lines: list[str] = []
    for _u, _v, data in sg.graph.edges(data=True):
        fk: models.FKEdge = data["fk"]
        key = (fk.from_table, fk.from_column, fk.to_table, fk.to_column)
        if key not in seen:
            seen.add(key)
            edge_lines.append(f"  {fk.from_table}.{fk.from_column} -> {fk.to_table}.{fk.to_column}")

    header = f"Graph: {len(blocks)} tables (nodes), {len(seen)} FK relationships (edges)"
    edges_block = "Edges (FK relationships):\n" + ("\n".join(edge_lines) or "  (none)")
    return f"{header}\n\n" + "\n\n".join(blocks) + "\n\n" + edges_block
