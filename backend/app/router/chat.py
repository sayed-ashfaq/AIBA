import asyncio
import uuid
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from pydantic import BaseModel, Field

from app.agents.orchestrator.context import AgentContext
from app.agents.orchestrator.deep_agent import agent
from app.agents.shared.progress import ProgressChannel, reduce_events, sse_event
from app.agents.verifier.verify import verify_turn
from app.core.dependencies import CurrentUser
from app.core.logging import get_logger, log_duration
from app.db.models import Chat, Message
from app.db.session import SessionDep
from app.services import chats as chat_service
from app.services import connections as connection_service
from app.services import results as result_service
from app.services.results import QueryData

router = APIRouter()
logger = get_logger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    # omit to start a new conversation — the response carries the id to use from then on
    chat_id: Optional[uuid.UUID] = None


class MessageResponse(BaseModel):
    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    sql: Optional[str] = None
    routed_to: Optional[str] = None
    # an independent review of the approach behind this turn — see app/agents/verifier. None for
    # turns that never delegated, or where the review itself failed; never re-derived on reopen,
    # since a review is a judgement about the steps taken, not something that goes stale like rows
    # would.
    reasoning: Optional[str] = None
    created_at: datetime
    # only filled in when a conversation is being reopened — see `of`
    data: Optional[QueryData] = None
    # the agent's activity trail for a streamed turn — one entry per tool call, in the shape the
    # client's ActivityTrail renders. Sent both on the just-run turn and on reopen, so the trail
    # survives a refresh or a chat switch. None for plain (non-streamed) turns and old rows.
    activity: Optional[list] = None

    @classmethod
    def of(cls, message: Message, with_data: bool = False) -> "MessageResponse":
        """`with_data` off by default so a payload is never sent twice in one response: the turn
        that just ran carries its rows on ChatResponse.data, in full, and reading them back off the
        row it was written to would only repeat half a megabyte."""
        return cls(
            id=message.id,
            role=message.role,
            content=message.content,
            sql=message.sql,
            routed_to=message.routed_to,
            reasoning=message.reasoning,
            created_at=message.created_at,
            data=result_service.from_storage(message.result_data) if with_data else None,
            activity=message.activity,
        )


class ChatResponse(BaseModel):
    chat_id: uuid.UUID
    # echoed so a client that just started a conversation can name it in the sidebar without
    # re-fetching the list
    title: str
    reply: str
    routed_to: str
    sql: Optional[str] = None
    reasoning: Optional[str] = None
    data: Optional[QueryData] = None
    message: MessageResponse


def _to_lc_messages(history: list[Message]) -> list[AnyMessage]:
    return [
        HumanMessage(content=m.content) if m.role == "user" else AIMessage(content=m.content)
        for m in history
    ]


def _final_reply(messages: list[AnyMessage]) -> str:
    """The orchestrator's last word to the user. Walks back to the last non-empty AIMessage rather
    than trusting messages[-1] — a provider occasionally emits a trailing empty end-turn message
    after the real answer, same reasoning deepagents' own task-tool return path uses."""
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.text:
            text = message.text.rstrip()
            if text:
                return text
    return ""


def _routed_to(messages: list[AnyMessage]) -> str:
    """Which subagent, if any, the orchestrator delegated to for this turn's answer. "orchestrator"
    when it answered directly — a greeting or a question that never needed data."""
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            if call["name"] == "task":
                return call["args"].get("subagent_type", "orchestrator")
    return "orchestrator"


async def _prepare_turn(
    request: ChatRequest, user: CurrentUser, session: SessionDep
) -> tuple[Chat, list[Message]]:
    """Everything that must happen before any LLM work: resolve or create the chat row (ownership
    checked here so posting into someone else's chat fails fast, not after 30s of inference) and
    load its history."""
    if request.chat_id is None:
        chat_row = await chat_service.create_chat(
            session,
            user.id,
            title=chat_service.derive_title(request.message),
            connection_id=user.active_connection_id,
        )
        return chat_row, []

    chat_row = await chat_service.get_owned_chat(session, user.id, request.chat_id)
    history = await chat_service.load_history(session, chat_row.id)
    return chat_row, history


def _agent_input(history: list[Message], message: str) -> dict:
    return {"messages": _to_lc_messages(history) + [HumanMessage(content=message)]}


async def _finalize_turn(
    session: SessionDep,
    chat_row: Chat,
    question: str,
    result: dict,
    reasoning: Optional[str],
    activity: Optional[list] = None,
) -> ChatResponse:
    """Turn the agent's raw result into the stored turn and the response. Shared by both endpoints
    so the streamed path and the plain path persist and reply identically. `activity` is the
    reduced tool-call trail — only the streamed path has one."""
    reply = _final_reply(result["messages"])
    routed_to = _routed_to(result["messages"])
    data = result_service.from_agent_files(result.get("files", {}))
    sql = result_service.sql_from_agent_files(result.get("files", {}))

    # committed only now: a failure earlier leaves no half-written turn, and an abandoned new chat
    # leaves no empty row
    _, assistant = await chat_service.append_turn(
        session,
        chat_row,
        question=question,
        answer=reply,
        sql=sql,
        routed_to=routed_to,
        result_data=result_service.for_storage(data),
        reasoning=reasoning,
        activity=activity,
    )

    return ChatResponse(
        chat_id=chat_row.id,
        title=chat_row.title,
        reply=reply,
        routed_to=routed_to,
        sql=sql,
        reasoning=reasoning,
        data=data,
        message=MessageResponse.of(assistant),
    )


async def _review(question: str, result: dict) -> Optional[str]:
    # separate call, after the answer is already decided: the verifier reviews what just happened,
    # it does not take part in producing it. Best-effort — see verify_turn's docstring — so a slow
    # or failed review costs the review, not the turn.
    with log_duration("Verifier review"):
        return await asyncio.to_thread(
            verify_turn, question, _final_reply(result["messages"]), result["messages"]
        )


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, user: CurrentUser, session: SessionDep) -> ChatResponse:
    logger.info("user %s: %s", user.id, request.message)

    # Resolved here, not inside the agent: the agent graph is synchronous and can neither await the
    # annotations read nor rebuild a connection. Stays None when the user has nothing active —
    # plenty of questions never reach sql_agent, and those must still work without a connection.
    db_context = await connection_service.get_active_db_context(session, user)
    chat_row, history = await _prepare_turn(request, user, session)

    with log_duration("Total query completion"):
        # the graph is sync and spends most of its time in blocking LLM/driver calls, so it runs on
        # a worker thread rather than stalling the event loop for the whole turn
        result = await asyncio.to_thread(
            agent.invoke,
            _agent_input(history, request.message),
            context=AgentContext(chat_id=chat_row.id, db_context=db_context),
        )

    reasoning = await _review(request.message, result)
    return await _finalize_turn(session, chat_row, request.message, result, reasoning)


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest, user: CurrentUser, session: SessionDep
) -> StreamingResponse:
    """Same turn as POST /chat, but the agent's tool calls are streamed to the client as they
    happen (Server-Sent Events) so it can show the work instead of a spinner. Frames:
      event: step  — one tool started / finished, or a named stage ("verifying")
      event: done  — the full ChatResponse payload, identical to what POST /chat returns
      event: error — the turn failed after streaming began; detail is safe to show
    """
    logger.info("user %s (stream): %s", user.id, request.message)

    db_context = await connection_service.get_active_db_context(session, user)
    chat_row, history = await _prepare_turn(request, user, session)
    agent_input = _agent_input(history, request.message)

    async def events():
        try:
            channel = ProgressChannel()
            context = AgentContext(
                chat_id=chat_row.id, db_context=db_context, emit=channel.emit
            )
            # kept alongside the stream so the same trail the client sees can be persisted on the
            # message — otherwise it's gone the moment the tab is refreshed or the chat switched
            trail: list[dict] = []
            with log_duration("Total query completion"):
                agent_task = asyncio.create_task(
                    asyncio.to_thread(agent.invoke, agent_input, context=context)
                )
                async for event in channel.drain(agent_task):
                    trail.append(event)
                    yield sse_event("step", event)
                result = agent_task.result()  # re-raises an agent failure

            verifying = {"type": "stage", "stage": "verifying"}
            trail.append(verifying)
            yield sse_event("step", verifying)
            reasoning = await _review(request.message, result)

            response = await _finalize_turn(
                session, chat_row, request.message, result, reasoning,
                activity=reduce_events(trail),
            )
            yield sse_event("done", response.model_dump(mode="json"))
        except Exception:
            logger.exception("chat stream failed for user %s", user.id)
            yield sse_event("error", {"detail": "internal error — check server logs"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
