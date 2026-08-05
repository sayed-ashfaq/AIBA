"""Persistence for conversations.

Before this existed the client held the only copy of a conversation and posted it back on every
request. That made history forgeable, capped it at one browser tab, and lost it on refresh. The
server now owns it; the client sends a chat id.

Scoped exactly like connections: every function takes the signed-in user and filters on their id.
"""

import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ChatNotFoundError
from app.core.logging import get_logger
from app.db.models import Chat, Message

logger = get_logger(__name__)

# How much of a conversation is replayed to the agent. Unbounded history would grow the prompt
# until it hit the model's context limit — and cost more on every turn to little effect, since the
# supervisor's routing decision leans on the last few exchanges rather than the whole transcript.
HISTORY_LIMIT = 20

TITLE_MAX_LENGTH = 60


def derive_title(message: str) -> str:
    """A chat's name is its opening question, trimmed. The first message is almost always the topic,
    so this reads well without costing an LLM call on the critical path; PATCH /chats/{id} is there
    for the times it doesn't."""
    title = " ".join(message.split())
    if len(title) > TITLE_MAX_LENGTH:
        title = title[: TITLE_MAX_LENGTH - 1].rstrip() + "…"
    return title or "New chat"


async def get_owned_chat(session: AsyncSession, user_id: uuid.UUID, chat_id: uuid.UUID) -> Chat:
    """The user's chat, or 404 — same-error-for-both, so this can't be used to probe for other
    people's chat ids."""
    chat = await session.scalar(
        select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id)
    )
    if chat is None:
        raise ChatNotFoundError
    return chat


async def create_chat(
    session: AsyncSession,
    user_id: uuid.UUID,
    title: str,
    connection_id: Optional[uuid.UUID] = None,
) -> Chat:
    chat = Chat(user_id=user_id, title=title, connection_id=connection_id)
    session.add(chat)
    await session.flush()  # assigns the id without ending the caller's transaction
    return chat


async def load_history(session: AsyncSession, chat_id: uuid.UUID) -> list[Message]:
    """The tail of a conversation, oldest-first.

    Ordered by seq DESC to let the index find the newest rows without reading the whole chat, then
    reversed in Python — the agent needs them chronologically.
    """
    rows = (
        await session.scalars(
            select(Message)
            .where(Message.chat_id == chat_id)
            .order_by(Message.seq.desc())
            .limit(HISTORY_LIMIT)
        )
    ).all()
    return list(reversed(rows))


async def load_last_result(session: AsyncSession, chat_id: uuid.UUID) -> Optional[dict]:
    """The most recent turn's rows, for a follow-up that wants to re-chart them rather than ask the
    database again. Most turns store nothing, so this looks past them rather than only at the last
    message — "make that a pie chart" after a couple of conversational replies still finds its data.
    """
    return await session.scalar(
        select(Message.result_data)
        .where(Message.chat_id == chat_id, Message.result_data.isnot(None))
        .order_by(Message.seq.desc())
        .limit(1)
    )


async def append_turn(
    session: AsyncSession,
    chat: Chat,
    question: str,
    answer: str,
    sql: Optional[str] = None,
    routed_to: Optional[str] = None,
    result_data: Optional[dict] = None,
) -> list[Message]:
    """Write a question and its answer as one unit.

    Both rows in a single commit, at the end of the turn: an agent failure part-way through then
    leaves nothing behind, rather than a dangling question with no reply.
    """
    next_seq = await session.scalar(
        select(func.coalesce(func.max(Message.seq), -1) + 1).where(Message.chat_id == chat.id)
    )

    messages = [
        Message(chat_id=chat.id, seq=next_seq, role="user", content=question),
        Message(
            chat_id=chat.id,
            seq=next_seq + 1,
            role="assistant",
            content=answer,
            sql=sql,
            routed_to=routed_to,
            result_data=result_data,
        ),
    ]
    session.add_all(messages)

    # touch the parent so the sidebar sorts by last use. onupdate only fires when a column actually
    # changes, and nothing on the chat row itself has.
    chat.updated_at = func.now()

    await session.commit()
    for message in messages:
        await session.refresh(message)
    return messages


def _summary(chat: Chat, message_count: int) -> dict:
    return {
        "id": chat.id,
        "title": chat.title,
        "connection_id": chat.connection_id,
        "message_count": message_count,
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
    }


async def list_chats(session: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    """Sidebar listing: newest activity first, without dragging every message along."""
    message_count = (
        select(func.count(Message.id))
        .where(Message.chat_id == Chat.id)
        .correlate(Chat)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(Chat, message_count.label("message_count"))
            .where(Chat.user_id == user_id)
            .order_by(Chat.updated_at.desc())
        )
    ).all()
    return [_summary(chat, count) for chat, count in rows]


async def summarise(session: AsyncSession, chat: Chat) -> dict:
    """One chat in listing shape, for endpoints that already hold the row."""
    count = await session.scalar(
        select(func.count(Message.id)).where(Message.chat_id == chat.id)
    )
    return _summary(chat, count or 0)


async def get_chat_with_messages(
    session: AsyncSession, user_id: uuid.UUID, chat_id: uuid.UUID
) -> Chat:
    """A whole conversation, for opening it in the UI. Every message, not just the agent's window —
    the user should see what they said, even beyond what the model is shown."""
    chat = await session.scalar(
        select(Chat)
        .where(Chat.id == chat_id, Chat.user_id == user_id)
        .options(selectinload(Chat.messages))
    )
    if chat is None:
        raise ChatNotFoundError
    return chat


async def rename_chat(
    session: AsyncSession, user_id: uuid.UUID, chat_id: uuid.UUID, title: str
) -> Chat:
    chat = await get_owned_chat(session, user_id, chat_id)
    chat.title = title
    await session.commit()
    await session.refresh(chat)
    return chat


async def delete_chat(session: AsyncSession, user_id: uuid.UUID, chat_id: uuid.UUID) -> None:
    chat = await get_owned_chat(session, user_id, chat_id)
    await session.delete(chat)  # messages follow by cascade
    await session.commit()
    logger.info("user %s deleted chat %s", user_id, chat_id)
