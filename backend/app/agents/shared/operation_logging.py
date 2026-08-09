"""One log line per tool call, written to the local log file only — which agent called which tool,
and with what: the SQL, the pandas/matplotlib code, the delegation instructions. Not LangSmith (that's
already wired up via .env and has the full call graph in a browser); this is for a plain `tail`/`grep`
on the log file to see what an agent actually did.

Logging arguments in full is safe here specifically because of a convention every subagent's own
prompt already enforces: nothing in this app calls a tool with row data as an argument — sql_agent,
python_agent, and visualizer are all told never to hardcode a data value into SQL or code, so an
argument is always logic (a query, a snippet, a task description, a file path), never a result.
Return values are the opposite — execute_sql's reply restates a sample of the actual rows — so this
deliberately never logs one; nothing here needs to.
"""

import time
from typing import Callable

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.core.logging import get_logger

logger = get_logger(__name__)

# the one argument name, across every tool in this app, that holds the actual query or code being
# run — pulled out and printed as its own indented block with real newlines, instead of sitting
# escaped inside a dict repr next to everything else. execute_sql's `sql` arrives already
# reformatted by sql.py's clean_sql (reindented, transpiled with pretty=True), so this doesn't
# reformat it again — it only stops the log line from mangling formatting that's already there.
_PRIMARY_ARG_NAMES = ("sql", "code")

# arguments big enough that logging them in full would bury the one thing worth seeing on this
# call — sql_generator gets the whole database schema every time; shown as a size, not the schema.
_ELIDED_ARGS = {"schema_context"}

# everything else long — a task() delegation's description, sql_generator's task — still gets
# shown, just cut to a preview rather than the full paragraph the orchestrator's own prompt asks
# for ("spell out the steps"). Enough to tell what was asked without the instruction text taking
# over the line; the actual SQL/code block is untouched by this, since seeing it in full is the
# point of the primary-arg handling below.
_MAX_ARG_LEN = 150


def _shorten(args: dict) -> dict:
    def clip(key: str, value: object) -> object:
        if key in _ELIDED_ARGS and isinstance(value, str):
            return f"<{len(value)} chars>"
        if isinstance(value, str) and len(value) > _MAX_ARG_LEN:
            return value[:_MAX_ARG_LEN] + "..."
        return value

    return {k: clip(k, v) for k, v in args.items()}


def _describe(tool_name: str, args: dict) -> tuple[str, str]:
    """(one-line call summary, an optional indented block to print under it)."""
    primary_key = next((name for name in _PRIMARY_ARG_NAMES if name in args), None)
    if primary_key is None:
        return f"{tool_name}({_shorten(args)})", ""

    rest = _shorten({k: v for k, v in args.items() if k != primary_key})
    summary = f"{tool_name}({rest})" if rest else tool_name
    body = "\n".join(f"    {line}" for line in str(args[primary_key]).splitlines())
    return summary, body


class OperationLoggingMiddleware(AgentMiddleware):
    """Attach once per agent, named for it — a subagent's middleware stack is entirely its own
    (same reasoning as ToolCallRetryMiddleware), so this has to be listed on the orchestrator and
    every subagent individually, not just declared once."""

    def __init__(self, agent_name: str) -> None:
        super().__init__()
        self.agent_name = agent_name

    def _log(self, summary: str, body: str, tail: str) -> None:
        line = f"{self.agent_name}: {summary} — {tail}"
        if body:
            line += f"\n{body}"
        logger.info(line)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], "ToolMessage | Command"],
    ) -> "ToolMessage | Command":
        summary, body = _describe(request.tool_call["name"], request.tool_call["args"])
        start = time.perf_counter()
        try:
            result = handler(request)
        except Exception:
            self._log(summary, body, f"failed after {time.perf_counter() - start:.2f}s")
            raise
        self._log(summary, body, f"{time.perf_counter() - start:.2f}s")
        return result
