"""Recovers from a recurring Groq/gpt-oss failure class: the model emits a tool call whose
arguments don't match the tool's declared schema — wrong parameter names, missing required fields.
This has hit both our own tools and deep-agents' built-ins (read_file, task) we don't control the
schema of, so it isn't fixable by renaming or re-describing any one tool. Groq validates this
server-side and rejects it with a 400 before the tool ever runs, which — unlike every other failure
in this system — normally crashes the whole turn with no chance for the model to see what went
wrong and correct it.

This middleware brings that failure into the same recoverable path everything else already uses:
one retry, with the validation error folded back in as a message, before giving up and re-raising.
Applied to every agent in this app (orchestrator and each subagent) since all of them run the same
model family and are equally exposed to it.
"""

from collections.abc import Callable
from typing import Optional

from groq import BadRequestError
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage

from app.core.logging import get_logger

logger = get_logger(__name__)


def _tool_schema_error(exc: BadRequestError) -> Optional[str]:
    """The validation error to hand back to the model, or None if this wasn't that failure class —
    a rate limit, an auth failure, or a real server error is left alone and re-raised untouched."""
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict) or error.get("code") != "tool_use_failed":
        return None
    return error.get("message") or str(exc)


class ToolCallRetryMiddleware(AgentMiddleware):
    """Retries a model call once when the provider rejects a malformed tool call, instead of
    letting that rejection crash the turn."""

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        try:
            return handler(request)
        except BadRequestError as exc:
            reason = _tool_schema_error(exc)
            if reason is None:
                raise
            logger.warning("retrying after a rejected tool call: %s", reason)
            corrective = HumanMessage(
                content=(
                    f"Your last tool call was rejected before it ran: {reason}. Check that tool's "
                    "exact parameter names in its description and call it again correctly."
                )
            )
            return handler(request.override(messages=[*request.messages, corrective]))
