"""Conventions and helpers for the orchestrator's private virtual filesystem: the path scheme
subagents use to hand data to each other — e.g. where the SQL agent writes result rows for the
visualizer and Python agent to read.

Separate from the durable Postgres persistence in services/results.py, which exists for cross-turn
re-charting rather than within-turn handoff between subagents.
"""

import json
import uuid
from typing import Optional

from deepagents.backends import StateBackend

RESULTS_DIR = "/results"
CHARTS_DIR = "/charts"


def write_result(columns: list[str], rows: list[dict], truncated: bool, sql: Optional[str] = None) -> str:
    """Write one result set to the virtual filesystem and return its path. Used by both sql_agent
    (sql set) and python_agent (sql=None — a computed result has none).

    A fresh path per call, not a fixed one — a single turn can delegate more than once (e.g.
    "compare this month to last month"), and each result needs its own file.

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


def read_result(path: str) -> dict:
    """Read back a payload written by write_result — e.g. python_agent loading a result sql_agent
    already fetched. Raises FileNotFoundError if nothing exists at the path (a typo, or a file from
    a different turn)."""
    result = StateBackend().read(path)
    if result.error or result.file_data is None:
        raise FileNotFoundError(result.error or f"no file at {path}")
    return json.loads(result.file_data["content"])


def write_chart(image_base64: str, title: str, caption: str) -> str:
    """Write one rendered chart to the virtual filesystem and return its path. Used by visualizer
    after run_chart renders a PNG in the sandbox — same reasoning as write_result: title and caption
    ride alongside the image because this file (not visualizer's intermediate tool-call text) is
    what services/results.py reads back after the turn ends.
    """
    path = f"{CHARTS_DIR}/{uuid.uuid4().hex[:8]}.json"
    payload = {"image_base64": image_base64, "title": title, "caption": caption}
    StateBackend().write(path, json.dumps(payload))
    return path


def read_chart(path: str) -> dict:
    """Read back a payload written by write_chart. Raises FileNotFoundError, same as read_result."""
    result = StateBackend().read(path)
    if result.error or result.file_data is None:
        raise FileNotFoundError(result.error or f"no file at {path}")
    return json.loads(result.file_data["content"])
