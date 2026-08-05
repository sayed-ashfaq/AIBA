"""The rows behind an answer — on the wire, in the database, and back into the agent.

One shape serves all three, which is the point: the client renders a chart from it, the message row
stores it, and a follow-up like "show that as a pie chart" reads it back rather than re-running the
query. Re-running would be slower and, worse, dishonest — the numbers would be today's, sitting
under yesterday's prose.

The two directions are deliberately unequal. Writing is best-effort: a payload that can't be stored
costs the chart on reload, never the answer itself. Reading is defensive: a payload written by an
older version of the spec must not be able to break opening a conversation.
"""

import json
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from app.agents.shared.files import RESULTS_DIR
from app.agents.subagents.sql_agent.db import QueryResult
from app.agents.subagents.visualizer.charts import ChartSpec
from app.agents.subagents.visualizer.profile import profile_result
from app.core.logging import get_logger

logger = get_logger(__name__)

# Ceiling on one stored payload. The row cap is already 5000, but rows vary enormously in width —
# four numeric columns is about half a megabyte, forty text columns is many times that, and only a
# byte budget bounds both. Nothing visible is lost at this size: the client plots at most 1000
# points and tables 200, so a result trimmed to fit renders identically to the one that ran.
MAX_STORED_BYTES = 256_000


class ColumnInfo(BaseModel):
    """One column's shape. The chart controls are built from this: which columns can be an axis,
    which can be a measure, and which have too many distinct values to be either."""

    name: str
    role: str  # "temporal" | "numeric" | "categorical"
    distinct: int
    nulls: int


class QueryData(BaseModel):
    """The rows behind an answer, when the turn ran a query."""

    columns: list[str]
    rows: list[dict]
    # what the query returned, which is not always how many rows are here: a payload trimmed to the
    # storage budget keeps this at the full count, so the client can say it is showing a sample
    # rather than presenting one as the whole result
    row_count: int
    # the result hit the row cap and there is likely more behind it — the client should say so
    # rather than presenting a capped result as complete
    truncated: bool
    # how to draw this, when there's a chart worth drawing. Null is ordinary — most answers are a
    # sentence, and a chart of them would be decoration.
    chart: Optional[ChartSpec] = None
    # sent whenever rows are, chart or not: it's what lets the client offer a different chart than
    # the one chosen here without asking the server again
    profile: list[ColumnInfo] = []


def _latest_result_payload(files: dict) -> Optional[dict]:
    """The most recently written /results/*.json payload in one turn's virtual filesystem, or None
    if sql_agent never queried data. A turn can delegate more than once (e.g. "compare this month
    to last month"), so more than one file can exist — the most recent one is what the
    orchestrator's final answer was actually grounded in."""
    candidates = {path: fd for path, fd in files.items() if path.startswith(RESULTS_DIR + "/")}
    if not candidates:
        return None
    latest_path = max(candidates, key=lambda p: candidates[p].get("modified_at") or candidates[p].get("created_at") or "")
    return json.loads(candidates[latest_path]["content"])


def from_agent_files(files: dict) -> Optional[QueryData]:
    """The rows behind one turn's answer, or None unless sql_agent actually queried data. No chart
    yet — that's the visualizer subagent's job, not built yet — so this is rows and their shape
    (via profile_result) only."""
    payload = _latest_result_payload(files)
    if payload is None:
        return None

    result = QueryResult(columns=payload["columns"], rows=payload["rows"], truncated=payload["truncated"])
    profile = profile_result(result)
    return QueryData(
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
        profile=[ColumnInfo(name=c.name, role=c.role, distinct=c.distinct, nulls=c.nulls) for c in profile.columns],
    )


def sql_from_agent_files(files: dict) -> Optional[str]:
    """The exact SQL behind the same result from_agent_files would return, or None. Kept separate
    from QueryData (which has never carried a sql field) rather than folded in, so ChatResponse's
    existing top-level `sql` field stays sourced the same way it always has."""
    payload = _latest_result_payload(files)
    return payload.get("sql") if payload else None


def _fit(rows: list[dict]) -> list[dict]:
    """Thin a payload to the storage budget by taking evenly spaced rows, first and last kept.

    Evenly spaced rather than the first N, for the same reason the client thins this way: a line
    chart cut off at its first N points silently redraws the x-axis to a fraction of the range and
    looks like a complete picture of it. Striding keeps the range and the shape of the curve, and
    only loses resolution between points.
    """
    if len(rows) < 3:
        return rows

    size = len(json.dumps(rows).encode())
    if size <= MAX_STORED_BYTES:
        return rows

    keep = max(2, int(len(rows) * MAX_STORED_BYTES / size))
    stride = (len(rows) - 1) / (keep - 1)
    thinned = [rows[round(i * stride)] for i in range(keep)]
    logger.info("trimmed a %d-row result to %d for storage (%d bytes)", len(rows), keep, size)
    return thinned


def for_storage(data: Optional[QueryData]) -> Optional[dict]:
    """The payload as a JSON column, trimmed to size — or None if it can't be stored at all.

    Best-effort on purpose. A value that survives the driver's conversion but not json.dumps would
    otherwise raise at commit, after the whole turn has been paid for, and lose the user their
    answer over a chart they may never open.
    """
    if data is None:
        return None

    try:
        stored = data.model_dump(mode="json")
        stored["rows"] = _fit(stored["rows"])
        return stored
    except (TypeError, ValueError) as exc:
        logger.warning("result payload not storable, keeping the answer without it: %s", exc)
        return None


def from_storage(raw: Optional[Any]) -> Optional[QueryData]:
    """A stored payload, or None if it no longer parses.

    Charts written by an earlier version of the spec are the expected failure here, and the right
    outcome is a conversation that opens without one — not a conversation that can't be opened.
    """
    if not raw:
        return None

    try:
        return QueryData.model_validate(raw)
    except ValidationError as exc:
        logger.info("dropping a stored result payload that no longer parses: %s", exc)
        return None


def to_query_result(data: QueryData) -> QueryResult:
    """Back into the shape the profiler and chart rules take, so a re-chart runs the same code path
    as the query that first produced these rows."""
    return QueryResult(columns=data.columns, rows=data.rows, truncated=data.truncated)
