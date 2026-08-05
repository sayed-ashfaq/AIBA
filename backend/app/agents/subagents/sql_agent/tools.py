"""Tools exposed to the SQL subagent: get_schema (the active connection's schema text) and
execute_sql (runs cleaned, capped SQL against it via sql.clean_and_execute).

Thin wrappers over db.py/sql.py — the destructive-write guard and row cap already live there and
are reused unchanged.
"""
