"""The rows behind an answer — on the wire, in the database, and back into the agent.

One shape serves all three, which is the point: the client shows a chart from it, the message row
stores it, and a follow-up like "show that as a pie chart" reads the rows back rather than
re-running the query. Re-running would be slower and, worse, dishonest — the numbers would be
today's, sitting under yesterday's prose.

The two directions are deliberately unequal. Writing is best-effort: a payload that can't be stored
costs the chart on reload, never the answer itself. Reading is defensive: a payload written by an
older version of the spec must not be able to break opening a conversation.
"""

import json
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from app.agents.shared.files import CHARTS_DIR, RESULTS_DIR
from app.agents.subagents.sql_agent.db import QueryResult
from app.agents.subagents.visualizer.profile import profile_result
from app.core.logging import get_logger

logger = get_logger(__name__)

# Ceiling on one stored payload. The row cap is already 5000, but rows vary enormously in width —
# four numeric columns is about half a megabyte, forty text columns is many times that, and only a
# byte budget bounds both. Nothing visible is lost at this size: the client plots at most 1000
# points and tables 200, so a result trimmed to fit renders identically to the one that ran.
MAX_STORED_BYTES = 256_000

# Ceiling on the chart image's own base64 payload, tracked separately from MAX_STORED_BYTES: a
# base64 PNG can't be thinned the way rows can, so a chart over budget is dropped outright rather
# than costing the whole turn's row data its storage.
MAX_CHART_BASE64_BYTES = 500_000


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
    # a chart the visualizer subagent drew from these rows, as a base64-encoded PNG the client can
    # show directly. None is ordinary — most answers are a sentence, and not every turn delegates
    # to visualizer at all.
    chart_image: Optional[str] = None
    chart_title: Optional[str] = None
    chart_caption: Optional[str] = None
    # sent whenever rows are, chart or not: it's what lets the client build its own controls (which
    # columns are measures, which are labels) without asking the server again
    profile: list[ColumnInfo] = []


def _latest(files: dict, directory: str) -> Optional[dict]:
    """The most recently written `{directory}/*.json` payload in one turn's virtual filesystem, or
    None if nothing was written there. A turn can delegate more than once (e.g. "compare this month
    to last month"), so more than one file can exist — the most recent one is what the
    orchestrator's final answer was actually grounded in."""
    candidates = {path: fd for path, fd in files.items() if path.startswith(directory + "/")}
    if not candidates:
        return None
    latest_path = max(candidates, key=lambda p: candidates[p].get("modified_at") or candidates[p].get("created_at") or "")
    return json.loads(candidates[latest_path]["content"])


def _latest_result_payload(files: dict) -> Optional[dict]:
    return _latest(files, RESULTS_DIR)


def _latest_chart_payload(files: dict) -> Optional[dict]:
    return _latest(files, CHARTS_DIR)


def from_agent_files(files: dict) -> Optional[QueryData]:
    """The rows (and, if visualizer ran, the chart) behind one turn's answer, or None unless
    sql_agent actually queried data. A chart is only ever attached alongside the rows it was drawn
    from — visualizer has no data of its own, so a chart file with no result file can't happen."""
    payload = _latest_result_payload(files)
    if payload is None:
        return None

    result = QueryResult(columns=payload["columns"], rows=payload["rows"], truncated=payload["truncated"])
    profile = profile_result(result)
    chart = _latest_chart_payload(files)
    return QueryData(
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
        chart_image=chart.get("image_base64") if chart else None,
        chart_title=chart.get("title") if chart else None,
        chart_caption=chart.get("caption") if chart else None,
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
        chart_image = stored.get("chart_image")
        if chart_image and len(chart_image.encode()) > MAX_CHART_BASE64_BYTES:
            logger.warning("dropping oversized chart image (%d bytes) from storage", len(chart_image.encode()))
            stored["chart_image"] = stored["chart_title"] = stored["chart_caption"] = None
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
