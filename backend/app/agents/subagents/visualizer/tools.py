"""The single tool exposed to the visualizer subagent: render_chart, which loads one or more result
files sql_agent/python_agent already wrote into pandas DataFrames, runs model-written
matplotlib/seaborn code against them in shared.sandbox's resource-limited subprocess, and writes the
rendered PNG as a new chart file — same virtual-filesystem convention execute_sql/run_data_code use
for their own results.

Replaces charts.py's rule-based candidate/select machinery: that system could only ever produce the
five chart types the frontend's Recharts renderer knew how to draw from a declarative spec. Handing
the model a real plotting library instead trades that fixed vocabulary for whatever matplotlib/
seaborn can draw — heatmaps, box plots, scatter with a regression line — at the cost of judging the
chart's fitness in code review rather than a fixed shape-matching rule.
"""

from typing import Optional

from langchain_core.tools import tool

from app.agents.shared.files import read_result, write_chart
from app.agents.shared.sandbox import run_chart as render_in_sandbox
from app.core.logging import get_logger

logger = get_logger(__name__)


@tool("render_chart")
def render_chart(
    code: str, title: str, caption: str, data_paths: Optional[list[str]] = None
) -> str:
    """Run matplotlib/seaborn code against one or more result files sql_agent or python_agent
    already wrote, and capture the chart it draws.

    Takes four arguments — `code`, `title`, `caption`, and `data_paths`. No other argument names
    are accepted.

    data_paths: the file path(s) already reported to you (e.g. "/results/a1b2c3d4.json") — one per
    dataset the chart needs. Required — this tool has no data of its own.

    code: plain matplotlib/seaborn (already imported as `plt` and `sns`) plus pandas/numpy,
    operating on `df`/`dfs` — `dfs` is the list of DataFrames in the order given, `df` is `dfs[0]`
    for the common single-dataset case. Draw with a plotting call — `sns.barplot`, `plt.plot`,
    `sns.heatmap`, `plt.scatter`, whatever the data calls for — against columns that actually exist
    in df/dfs. The current figure is captured automatically once your code finishes; do not call
    `plt.show()` or `plt.savefig()` yourself. Never hardcode a value that should come from df/dfs.

    title: a short chart title in the language of the question, e.g. "Revenue by month".
    caption: one sentence on what the chart shows or the takeaway — state only what's visible in
    the data you plotted, never a number or trend that isn't actually there.

    Returns an error to fix and retry with, or a confirmation with the file path holding the chart.
    """
    if not data_paths:
        return (
            "No data_paths given. This tool has no data of its own — call it with the file "
            "path(s) already reported to you (e.g. \"/results/a1b2c3d4.json\"), not hardcoded "
            "numbers. If you don't have a path, you don't have the data yet — say so."
        )

    try:
        datasets = [read_result(path) for path in data_paths]
    except FileNotFoundError as exc:
        return f"Could not read input: {exc}"

    outcome = render_in_sandbox(code, [{"columns": d["columns"], "rows": d["rows"]} for d in datasets])
    if outcome.error:
        logger.info("render_chart failed: %s", outcome.error)
        return f"Error: {outcome.error}"

    path = write_chart(outcome.image_base64, title, caption)
    logger.info("render_chart: written to %s", path)
    return f'Chart "{title}" written to {path}.'
