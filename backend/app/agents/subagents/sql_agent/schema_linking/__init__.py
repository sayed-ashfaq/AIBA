"""Graph-based schema linking for NL2SQL.

Maps a natural-language question to the minimal slice of the database schema
needed to answer it: embeddings pick entry-point ("anchor") tables, and a
foreign-key graph connects them with real join paths — including bridge tables
that have no name similarity to the question but are structurally required.

Built alongside the flat-schema path (db.render_schema_text), selectable per
user. This module is the public surface; import submodules directly only for
internals (introspection, graph, embeddings).

    sg = build_schema_graph(engine)          # once per connection, then cache
    focused = link("revenue per supplier", sg)
    text = render_focused(focused, sg)        # -> hand to sql_generator
"""

from app.agents.subagents.sql_agent.schema_linking.builder import build_schema_graph
from app.agents.subagents.sql_agent.schema_linking.linking import link
from app.agents.subagents.sql_agent.schema_linking.models import FocusedSchema, SchemaGraph
from app.agents.subagents.sql_agent.schema_linking.render import render_focused, render_graph_text

__all__ = [
    "build_schema_graph",
    "link",
    "render_focused",
    "render_graph_text",
    "SchemaGraph",
    "FocusedSchema",
]
