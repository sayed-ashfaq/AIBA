"""Conventions and helpers for the orchestrator's private virtual filesystem: the path scheme
subagents use to hand data to each other — e.g. where the SQL agent writes result rows for the
visualizer and Python agent to read.

Separate from the durable Postgres persistence in services/results.py, which exists for cross-turn
re-charting rather than within-turn handoff between subagents.
"""

import json
import uuid

from deepagents.backends import StateBackend

RESULTS_DIR = "/results"


def write_result(columns: list[str], rows: list[dict], truncated: bool, sql: str) -> str:
    """Write one query's full result set to the virtual filesystem and return its path.

    A fresh path per call, not a fixed one — a single turn can delegate to sql_agent more than
    once (e.g. "compare this month to last month"), and each result needs its own file.

    sql is carried alongside the rows because it's the only place the router can recover it after
    the fact — a subagent's own tool calls aren't visible in the orchestrator's message history,
    only its final text answer is, so this file (not that text) is what the API response's `sql`
    field is read back from. See services/results.py's from_agent_files.

    StateBackend reads the ambient LangGraph config to find the running graph's `files` channel,
    so this only works called from inside a tool during a graph run — same constraint the built-in
    read_file/write_file tools have.
    """
    path = f"{RESULTS_DIR}/{uuid.uuid4().hex[:8]}.json"
    payload = {"columns": columns, "rows": rows, "truncated": truncated, "sql": sql}
    StateBackend().write(path, json.dumps(payload))
    return path
