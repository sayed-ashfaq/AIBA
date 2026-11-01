"""The two tools exposed to the visualizer subagent: inspect_data, which shows the shape of one result
file, and render_chart, which takes an OpenUI Lang program, checks it, and stores it for the browser
to render against that result's rows.

Replaces the matplotlib sandbox. The model no longer draws pixels from code it has to get right
against a DataFrame; it writes a short declarative program naming components and columns, and the
frontend binds it to the rows (see frontend OpenUIView). That trades "anything matplotlib can draw"
for the component vocabulary in frontend/src/openui/library.js, and moves the "did it work" check
from running code to the static checks below plus the renderer's own error reporting.

The checks exist because the part of a program most likely to be wrong is also the part nothing
downstream would catch loudly: a column name that isn't in the data renders as an empty chart, not
an error, and numbers typed in by the model look exactly like numbers read from the data.
"""

import re
from typing import Optional

from langchain_core.tools import tool

from app.agents.shared.files import read_result, write_openui_chart
from app.agents.subagents.sql_agent.db import QueryResult
from app.agents.subagents.visualizer.profile import profile_result
from app.core.logging import get_logger

logger = get_logger(__name__)

# the one tool the program may Query — keep in step with ROWS_TOOL in frontend/src/openui/library.js
ROWS_TOOL = "get_rows"

_QUERY_CALL_RE = re.compile(r'Query\(\s*"([^"]*)"')
_ROWS_QUERY_RE = re.compile(r'(\w+)\s*=\s*Query\(\s*"' + ROWS_TOOL + '"')
_ROOT_RE = re.compile(r"^\s*root\s*=", re.MULTILINE)
# three or more bare numbers in a row inside one array: a typed-in data series, not an axis setting
_NUMBER_LIST_RE = re.compile(r"\[\s*-?\d+(?:\.\d+)?(?:\s*,\s*-?\d+(?:\.\d+)?){2,}\s*\]")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_]\w*$")

_NOT_A_RESULT = (
    "{path} is not a query result file. Result files look like /results/a1b2c3d4.json — use the "
    "exact path from your task, or `ls /results` to see what exists. If there isn't one for this "
    "data, say the data hasn't been retrieved yet."
)


def _load(data_paths: Optional[list[str]]) -> tuple[Optional[QueryResult], Optional[str]]:
    """The one dataset to draw from, or an error message for the model."""
    if not data_paths:
        return None, (
            "No data_paths given. This tool has no data of its own — call it with the file "
            "path(s) already reported to you (e.g. \"/results/a1b2c3d4.json\"). If you don't have a "
            "path, you don't have the data yet — say so."
        )
    if len(data_paths) != 1:
        return None, (
            f"Got {len(data_paths)} paths, but a chart is drawn from exactly one dataset for now. "
            "Draw from the one that answers the task, and say in your final answer that the other "
            "was not charted."
        )
    try:
        payload = read_result(data_paths[0])
    except FileNotFoundError as exc:
        return None, f"Could not read input: {exc}"
    except ValueError:
        # not JSON at all — e.g. a CSV some agent wrote under /results. Handed back as an error
        # rather than raised, which would end the whole turn over one wrong path.
        return None, _NOT_A_RESULT.format(path=data_paths[0])
    if not isinstance(payload, dict) or not {"columns", "rows", "truncated"} <= payload.keys():
        return None, _NOT_A_RESULT.format(path=data_paths[0])
    return QueryResult(columns=payload["columns"], rows=payload["rows"], truncated=payload["truncated"]), None


def check_program(program: str, columns: list[str]) -> list[str]:
    """What is wrong with this program, as messages the model can act on. Empty means it passed."""
    problems = []

    if not _ROOT_RE.search(program):
        problems.append("No `root = ...` statement. Every program starts with `root = Card([...])`.")

    for tool_name in _QUERY_CALL_RE.findall(program):
        if tool_name != ROWS_TOOL:
            problems.append(f'Query("{tool_name}") is not a tool. The only one is "{ROWS_TOOL}".')

    query_names = _ROWS_QUERY_RE.findall(program)
    if not query_names:
        problems.append(
            f'The program never reads the data. Add `data = Query("{ROWS_TOOL}", {{}}, {{rows: []}})` '
            "and plot data.rows.<column>."
        )

    known = set(columns)
    for name in query_names:
        for column in dict.fromkeys(re.findall(rf"\b{re.escape(name)}\.rows\.(\w+)", program)):
            if column not in known:
                problems.append(f"`{name}.rows.{column}`: there is no column `{column}`. Columns: {', '.join(columns)}.")

    if _NUMBER_LIST_RE.search(program):
        problems.append(
            "A list of typed-in numbers. Every plotted value must be read from the data "
            "(data.rows.<column>), never written into the program."
        )

    return problems


@tool("inspect_data")
def inspect_data(data_paths: Optional[list[str]] = None) -> str:
    """Show the columns of a result file sql_agent or analytics_agent already wrote: each column's
    exact name, what kind of values it holds (temporal, numeric, categorical), how many distinct
    values, and its range. Call this before render_chart so the program uses real column names.

    data_paths: the file path(s) already reported to you (e.g. "/results/a1b2c3d4.json").
    """
    result, error = _load(data_paths)
    if error:
        return error

    profile = profile_result(result)
    unreferenceable = [c for c in result.columns if not _IDENTIFIER_RE.match(c)]
    note = ""
    if unreferenceable:
        note = (
            "\n\nThese column names contain characters a program cannot reference, so they cannot "
            f"be plotted: {', '.join(unreferenceable)}. Say so rather than guessing a name."
        )
    return f"Columns of {data_paths[0]} (use these exact names after `.rows.`):\n{profile.describe()}{note}"


@tool("render_chart")
def render_chart(program: str, title: str, caption: str, data_paths: Optional[list[str]] = None) -> str:
    """Check an OpenUI Lang program and store it as the chart for this answer. The browser renders it
    against the rows of the result file — the program names columns, it never contains data.

    Takes four arguments — `program`, `title`, `caption`, and `data_paths`. No other argument names
    are accepted.

    program: the complete OpenUI Lang program, per the language reference in your instructions.
    title: a short chart title in the language of the question, e.g. "Revenue by month".
    caption: one sentence on what the chart shows or the takeaway — state only what the columns you
    plotted can show, never a number or trend you were not given.
    data_paths: the file path already reported to you (e.g. "/results/a1b2c3d4.json"). Required.

    Returns the problems to fix and retry with, or a confirmation with the file path holding the chart.
    """
    result, error = _load(data_paths)
    if error:
        return error

    problems = check_program(program, result.columns)
    if problems:
        logger.info("render_chart rejected program: %s", problems)
        return "Fix these and call render_chart again:\n- " + "\n- ".join(problems)

    path = write_openui_chart(program, title, caption)
    logger.info("render_chart: written to %s", path)
    return f'Chart "{title}" written to {path}.'
