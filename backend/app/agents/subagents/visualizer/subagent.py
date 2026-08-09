"""The visualizer subagent's deep-agents config: name, description (what the orchestrator sees when
deciding to delegate here), system prompt, and its one tool (render_chart).

Runs as a free-form ReAct loop, same shape as python_agent — draw, read the sandbox's error if any,
retry, guided by prompt.py's retry cap. See tools.py for why this replaced charts.py's rule-based
picker.

ToolCallRetryMiddleware: Groq occasionally rejects a malformed tool call (wrong/missing parameter
names) from this model family with a hard 400 that would otherwise crash the turn — see its
docstring. A subagent's middleware is entirely its own; it doesn't inherit whatever the main agent
or another subagent declares, so this has to be listed here too, not just on the orchestrator.

ToolCallLimitMiddleware enforces prompt.py's "up to 3 attempts" as a hard cap rather than a
convention the model is trusted to count on its own — see python_agent/subagent.py's docstring for
the full reasoning; the same applies here for render_chart.

OperationLoggingMiddleware writes one local log line per render_chart call, naming this agent and
including the exact matplotlib/seaborn code run, so `grep`ing the log file shows what ran without
opening LangSmith.
"""

from deepagents import SubAgent
from langchain.agents.middleware import ToolCallLimitMiddleware

from app.agents.shared.operation_logging import OperationLoggingMiddleware
from app.agents.shared.tool_call_retry import ToolCallRetryMiddleware
from app.agents.subagents.visualizer.prompt import VISUALIZER_PROMPT
from app.agents.subagents.visualizer.tools import render_chart
from app.core.llm import get_llm

visualizer: SubAgent = {
    "name": "visualizer",
    "description": (
        "Draws one chart from data sql_agent or python_agent already retrieved — it has no "
        "database access of its own. Use when the user asks to see, plot, chart, or visualize "
        "something, or when a trend/comparison/distribution would genuinely be clearer as a chart "
        "than a sentence. Give it the file path(s) already reported and a precise description of "
        "what the chart should show, not the raw business question. Not every answer needs one — "
        "skip it for a single number or a short table."
    ),
    "system_prompt": VISUALIZER_PROMPT,
    "tools": [render_chart],
    "model": get_llm("visualizer"),
    "middleware": [
        ToolCallRetryMiddleware(),
        ToolCallLimitMiddleware(tool_name="render_chart", run_limit=3, exit_behavior="continue"),
        OperationLoggingMiddleware("visualizer"),
    ],
}
