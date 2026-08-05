"""pgvector-backed retrieval over ingested company documents, embedded with fastembed — the same
embedding library schema_graph.py already uses for schema search, so this adds a vector column and
a query path rather than a new embedding dependency.

Ingestion (chunking, embedding and storing documents) is a separate concern from this module, which
only serves queries.
"""
