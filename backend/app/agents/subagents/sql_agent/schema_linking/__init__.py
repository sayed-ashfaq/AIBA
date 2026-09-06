"""Graph-based schema linking for NL2SQL.

Maps a natural-language question to the minimal slice of the database schema
needed to answer it: embeddings pick entry-point ("anchor") tables, and a
foreign-key graph connects them with real join paths — including bridge tables
that have no name similarity to the question but are structurally required.

Built alongside the flat-schema path (db.render_schema_text), selectable per
user. The public API (build_schema_graph, link, render_*) is re-exported here
once the pieces exist (Step 6); until then, import the submodules directly.
"""
