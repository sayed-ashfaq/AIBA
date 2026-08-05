"""Runtime context for one agent invocation: the requesting user's active database connection and
chat id — everything a subagent's tools need but that shouldn't live in conversation state or be
re-derived mid-run.

Replaces most of main_agent/state.py's AgentState. Deep agents carries its own message history and
virtual filesystem, so the only state this app still threads through by hand is what a subgraph
literally cannot look up itself — the live DB connection, same reasoning as db.py's DbContext.

Passed to create_deep_agent as context_schema and set per-request via `.invoke(..., context=...)`.
LangGraph threads it to every tool in the run, main agent and subagents alike, via ToolRuntime.context
— so sql_agent's tools see the same db_context without it being re-sent through conversation state.
"""

import uuid
from dataclasses import dataclass
from typing import Optional

from app.agents.subagents.sql_agent.db import DbContext


@dataclass
class AgentContext:
    chat_id: uuid.UUID
    # None when the user has nothing active — plenty of questions never touch the database, and
    # those must still work without a connection. Resolved by the router before invoke(), since
    # deep agents runs synchronously and can't await the annotations read mid-turn.
    db_context: Optional[DbContext] = None
