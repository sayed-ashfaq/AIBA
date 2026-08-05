import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Chat(Base):
    """One conversation. Private to its owner, and deleted with them."""

    __tablename__ = "chats"
    __table_args__ = (
        # the sidebar query is "my chats, most recently used first" — this index serves it whole,
        # so listing stays cheap as a user's history grows
        Index("ix_chats_user_id_updated_at", "user_id", text("updated_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # no index=True: ix_chats_user_id_updated_at leads with this column, so a standalone index
    # would be dead weight postgres still has to maintain on every write
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )

    # which database the conversation was started against, for display. SET NULL rather than
    # CASCADE: deleting a connection must not delete the conversations that referenced it, and the
    # messages stay meaningful without it.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("saved_connections.id", ondelete="SET NULL"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # bumped on every new message, so "recent" means recently *used*, not recently created
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="chat",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.seq",
    )


class Message(Base):
    """One turn in a conversation — a question from the user, or an answer from the agent."""

    __tablename__ = "messages"
    __table_args__ = (
        # doubles as the index for "this chat's messages in order", which is why chat_id carries no
        # index of its own — the unique constraint's index already leads with it
        UniqueConstraint("chat_id", "seq", name="uq_messages_chat_seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    chat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chats.id", ondelete="CASCADE")
    )

    # Ordering key, and the reason created_at isn't one: a question and its answer are written in
    # the same transaction, and postgres' now() is transaction time — both rows would carry an
    # identical timestamp and the conversation would render in arbitrary order.
    seq: Mapped[int] = mapped_column(Integer)

    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)

    # assistant turns only: the SQL that produced the answer, and which agent handled it. Kept so a
    # reopened conversation still shows its query, exactly as it looked when first answered.
    sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    routed_to: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # assistant turns that ran a query: the rows behind the answer, in the same shape the API sends
    # them — columns, rows, the chart chosen for them, and the profile the chart controls are built
    # from. Stored because a chart is the part of an answer worth coming back to, and re-running the
    # query on open would be both slow and a lie: the numbers would be today's, under yesterday's
    # prose. Trimmed to a size bound before it gets here; see results.for_storage.
    #
    # JSONB rather than Text so this stays queryable later (which turns had charts, of what type)
    # without a migration to go looking.
    result_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chat: Mapped["Chat"] = relationship(back_populates="messages")
