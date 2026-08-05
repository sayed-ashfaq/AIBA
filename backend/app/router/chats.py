"""Conversation history: the sidebar list, and opening or managing one chat.

Sending a message lives in app.router.chat — it belongs with the agent invocation rather than here.
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.dependencies import CurrentUser
from app.core.logging import get_logger
from app.db.session import SessionDep
from app.router.chat import MessageResponse
from app.services import chats as chat_service

router = APIRouter(prefix="/chats", tags=["chats"])
logger = get_logger(__name__)


class ChatSummary(BaseModel):
    id: uuid.UUID
    title: str
    connection_id: Optional[uuid.UUID] = None
    message_count: int
    created_at: datetime
    updated_at: datetime


class ChatDetail(BaseModel):
    id: uuid.UUID
    title: str
    connection_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime
    messages: list[MessageResponse]


class RenameChatRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)


@router.get("", response_model=list[ChatSummary])
async def get_chats(user: CurrentUser, session: SessionDep) -> list[ChatSummary]:
    """Most recently used first — the order a sidebar wants."""
    return [ChatSummary(**row) for row in await chat_service.list_chats(session, user.id)]


@router.get("/{chat_id}", response_model=ChatDetail)
async def get_chat(chat_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> ChatDetail:
    chat = await chat_service.get_chat_with_messages(session, user.id, chat_id)
    return ChatDetail(
        id=chat.id,
        title=chat.title,
        connection_id=chat.connection_id,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        # with_data: this is the reopening path, and the chart is the part of an old answer worth
        # coming back to
        messages=[MessageResponse.of(m, with_data=True) for m in chat.messages],
    )


@router.patch("/{chat_id}", response_model=ChatSummary)
async def rename_chat(
    chat_id: uuid.UUID, request: RenameChatRequest, user: CurrentUser, session: SessionDep
) -> ChatSummary:
    chat = await chat_service.rename_chat(session, user.id, chat_id, request.title)
    return ChatSummary(**await chat_service.summarise(session, chat))


@router.delete("/{chat_id}")
async def remove_chat(chat_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> dict:
    await chat_service.delete_chat(session, user.id, chat_id)
    return {"deleted": str(chat_id)}
