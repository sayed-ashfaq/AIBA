"""Independent review of one turn: not a step in producing the answer, a check on it afterward.

Deliberately not a deepagents subagent wired into the orchestrator's graph — the whole point is to
look at what the orchestrator and its subagents did from outside that loop, the way a monitor
observes rather than participates. Wiring it in as another `task()` delegation would let it see only
what the orchestrator chose to tell it, the same limited view every other subagent gets; running it
here instead, against the orchestrator's own message list, lets it see the real delegations and what
actually came back from each one.

Scoped to one turn, not the conversation: `_delegations` reads this turn's `task` tool calls and
their replies straight off the orchestrator's message list, never the prior turns also present in
that list. Prior turns come back from `_to_lc_messages` (router/chat.py) as plain HumanMessage/
AIMessage with no tool_calls, so they never match `_delegations`'s filter — no separate slicing
needed to keep this to "this turn's steps," matching the "just the information per message, not the
full conversation history" scope this was asked to have.

Each subagent's reply text already carries what this review needs to judge the approach — sql_agent
states the exact SQL it ran, python_agent and visualizer state what they computed or drew from —
because their own prompts already require that in their final answer (see each subagent's prompt.py).
Nothing here re-reads a result file or re-derives anything; it only reads what the orchestrator
itself already received.

Best-effort like the rest of what isn't the answer itself (see services/results.py's for_storage): a
bad completion, a timeout, or a malformed response costs this turn's review, never the turn — the
user still gets their answer.
"""

from typing import Optional

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage

from app.agents.verifier.prompt import VERIFIER_PROMPT
from app.core.llm import get_llm
from app.core.logging import get_logger

logger = get_logger(__name__)


def _delegations(messages: list[AnyMessage]) -> list[dict]:
    """This turn's task() calls, each paired with the subagent's reply — the same task/subagent_type
    args and ToolMessage reply router/chat.py's _routed_to already reads, just kept instead of
    discarded after finding the last one."""
    replies = {m.tool_call_id: m.text for m in messages if isinstance(m, ToolMessage)}
    delegations = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            if call["name"] != "task":
                continue
            delegations.append(
                {
                    "subagent": call["args"].get("subagent_type", "?"),
                    "task": call["args"].get("description", ""),
                    "outcome": replies.get(call["id"], ""),
                }
            )
    return delegations


def _trace_text(question: str, answer: str, delegations: list[dict]) -> str:
    lines = [f"User's question: {question}"]
    for i, step in enumerate(delegations, start=1):
        lines += [
            "",
            f"Step {i} — delegated to {step['subagent']}, asked: {step['task']}",
            f"It came back with: {step['outcome']}",
        ]
    lines += ["", f"AIBA's final answer to the user: {answer}"]
    return "\n".join(lines)


def verify_turn(question: str, answer: str, messages: list[AnyMessage]) -> Optional[str]:
    """A short, plain-language review of this turn's approach, or None when there's nothing to
    review (the turn never delegated — a greeting, or a question answered from conversation alone,
    has no approach to check) or the review call itself failed."""
    delegations = _delegations(messages)
    if not delegations:
        return None

    trace = _trace_text(question, answer, delegations)
    try:
        response = get_llm("verifier").invoke(
            [SystemMessage(content=VERIFIER_PROMPT), HumanMessage(content=trace)]
        )
    except Exception as exc:  # best-effort — see module docstring
        logger.warning("verifier call failed, dropping the review for this turn: %s", exc)
        return None

    text = response.text.strip()
    return text or None
