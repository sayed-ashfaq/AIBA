"""The single tool exposed to the python subagent: run_python, which loads one or more result
files sql_agent already wrote into pandas DataFrames, runs model-written code against them in
shared.sandbox's resource-limited subprocess, and writes whatever comes back as a new result file —
same shape and same virtual-filesystem convention sql_agent's execute_sql uses.

Deliberately NOT registered as "python" (tried that — see git history): gpt-oss models were trained
on OpenAI's "harmony" format, which bakes in a strong prior for what a tool named exactly "python"
looks like (OpenAI's own hosted code-interpreter shape). That prior can override the JSON schema we
actually provide for our tool of the same name, producing calls with invented arguments
(file_path/operation/description-shaped, not our real code/data_paths) that Groq's strict
validation then rejects. Naming it something the model has no baked-in expectations for removes the
trigger for that override rather than just hoping the real schema wins.
"""

from typing import Optional

from langchain_core.tools import tool

from app.agents.shared.files import read_result, write_result
from app.agents.shared.sandbox import run_python as run_in_sandbox
from app.core.logging import get_logger

logger = get_logger(__name__)

# defensive cap on the written output — python_agent's job is computing on data already capped at
# MAX_ROWS by the SQL layer, so a result bigger than this means the code did something unexpected
# (e.g. an accidental cross join) rather than a legitimate "top N"-sized answer
MAX_ROWS_OUT = 5000

# results at or under this many rows are inlined in full in the tool's return value, same reasoning
# as execute_sql's threshold — a fixed small sample can silently cut off part of the real answer
FULL_INLINE_THRESHOLD = 20
SAMPLE_ROWS = 10


@tool("run_data_code")
def run_python(code: str, data_paths: Optional[list[str]] = None) -> str:
    """Run pandas/numpy code against one or more result files sql_agent already wrote.

    Takes exactly two arguments — `code` (a string of pandas/numpy) and `data_paths` (a list of
    file path strings). No other argument names are accepted; do not invent a `file_path`,
    `operation`, or `description` argument — those do not exist on this tool.

    data_paths: the file path(s) sql_agent reported (e.g. "/results/a1b2c3d4.json") — one per
    dataset the code needs. Required — this tool has no data of its own. Inside the sandbox, each
    path is loaded into a DataFrame; `dfs` is the list in the order given, and `df` is `dfs[0]` for
    the common single-dataset case.

    code: plain pandas/numpy operating on df/dfs — only those two plus math/statistics/datetime/
    json/decimal from the standard library are importable. Never hardcode data values in the code;
    everything must come from df/dfs. Assign your final answer to `result_df` (a DataFrame, or
    anything pd.DataFrame() accepts, e.g. a single-row dict for a scalar answer) — nothing else is
    returned. Runs in a subprocess with a timeout and memory cap.

    Returns an error to fix and retry with, or a summary, the result rows, and the file path
    holding the full result.
    """
    # a default rather than a required param: some models omit an argument entirely rather than
    # passing an empty list on a bad call, and a provider-side schema-validation rejection of that
    # raises before this function ever runs — surfacing as a hard 500 with no chance to retry.
    # Returning a normal, readable error here instead keeps that failure inside the ReAct loop.
    if not data_paths:
        return (
            "No data_paths given. This tool has no data of its own — call it with the file "
            "path(s) sql_agent already wrote (e.g. \"/results/a1b2c3d4.json\"), not hardcoded "
            "numbers. If you don't have a path, you don't have the data yet — say so."
        )

    try:
        datasets = [read_result(path) for path in data_paths]
    except FileNotFoundError as exc:
        return f"Could not read input: {exc}"

    outcome = run_in_sandbox(code, [{"columns": d["columns"], "rows": d["rows"]} for d in datasets])
    if outcome.error:
        logger.info("run_python failed: %s", outcome.error)
        return f"Error: {outcome.error}"

    columns, rows = outcome.columns, outcome.rows
    truncated = len(rows) > MAX_ROWS_OUT
    if truncated:
        rows = rows[:MAX_ROWS_OUT]

    path = write_result(columns, rows, truncated)
    logger.info("run_python: %d row(s), truncated=%s, written to %s", len(rows), truncated, path)

    is_full = len(rows) <= FULL_INLINE_THRESHOLD
    sample = rows if is_full else rows[:SAMPLE_ROWS]
    lines = [
        f"{len(rows)} row(s) returned"
        + (" (capped — result was larger)" if truncated else "")
        + f". Columns: {', '.join(columns)}.",
        f"Full result set written to {path}.",
        f"All {len(sample)} row(s):"
        if is_full
        else f"First {len(sample)} of {len(rows)} row(s) — this is a partial sample, read {path} for the rest before answering:",
        *(str(row) for row in sample),
    ]
    return "\n".join(lines)
