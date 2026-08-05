"""The SQL subagent's deep-agents config: name, description (what the orchestrator sees when
deciding to delegate here), system prompt, and its two tools (get_schema, execute_sql).

Runs as a free-form ReAct loop rather than the old generate/execute/fix subgraph in agents.py — the
model calls execute_sql, reads its own errors, and retries, guided by prompt.py's retry cap rather
than a hardcoded conditional edge.
"""
