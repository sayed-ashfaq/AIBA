"""Keeps the visualizer from ending its turn without having drawn anything.

The failure this guards against was seen live: after inspect_data returned the column list, the
model wrote a closing message ("here's your chart…") and stopped, never calling render_chart. The
orchestrator took that at its word and told the user a chart was attached; the browser had nothing
to show. Nothing downstream can tell a model that stopped early from one that finished, so the
check lives here, where the evidence is — whether a render_chart call actually succeeded.

It nudges rather than fails: the model gets a message and another turn. A visualizer that truly
can't chart the data (no usable columns, no path) is allowed to say so; the nudge asks for exactly
that, and the cap keeps a model that keeps declining from looping.
"""

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_NUDGES = 2

# marks our own messages so the cap can count them — a model never writes this prefix itself
_NUDGE_PREFIX = "[no chart yet]"

_NUDGE = (
    _NUDGE_PREFIX + " You finished without a successful render_chart call, so no chart exists — "
    "anything you said about one is not true. If the data can be charted, call render_chart now "
    "with the program, title, caption and data_paths. If it truly cannot be charted, say so "
    "plainly in your final answer and do not describe a chart."
)


def _chart_was_drawn(messages: list) -> bool:
    return any(
        isinstance(m, ToolMessage) and m.name == "render_chart" and str(m.content).startswith('Chart "')
        for m in messages
    )


class RequireChartMiddleware(AgentMiddleware):
    @hook_config(can_jump_to=["model"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        messages = state["messages"]
        last = messages[-1]
        # a tool call is still pending — the model isn't done
        if not isinstance(last, AIMessage) or last.tool_calls:
            return None
        if _chart_was_drawn(messages):
            return None

        nudges = sum(1 for m in messages if isinstance(m, HumanMessage) and str(m.content).startswith(_NUDGE_PREFIX))
        if nudges >= MAX_NUDGES:
            logger.warning("visualizer finished without a chart after %d nudges", nudges)
            return None

        logger.info("visualizer tried to finish without drawing a chart — sending it back (nudge %d)", nudges + 1)
        return {"messages": [HumanMessage(content=_NUDGE)], "jump_to": "model"}
