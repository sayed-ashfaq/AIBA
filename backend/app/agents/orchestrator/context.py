"""Runtime context for one agent invocation: the requesting user's active database connection and
chat id — everything a subagent's tools need but that shouldn't live in conversation state or be
re-derived mid-run.

Replaces most of main_agent/state.py's AgentState. Deep agents carries its own message history and
virtual filesystem, so the only state this app still threads through by hand is what a subgraph
literally cannot look up itself — the live DB connection, same reasoning as db.py's DbContext.
"""
