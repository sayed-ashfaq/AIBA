import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SavedConnection(Base):
    """A target database a user has registered. `url_encrypted` holds the full SQLAlchemy URL,
    credentials included, Fernet-encrypted — it is never returned to the client.

    Private to its owner: every query in app.services.connections filters on user_id, and there is
    no sharing mechanism. Deleting a user takes their connections (and, by cascade, the annotations
    on them) with it.
    """

    __tablename__ = "saved_connections"
    __table_args__ = (
        # per user, not globally — two people should both be allowed a connection called "prod"
        UniqueConstraint("user_id", "name", name="uq_saved_connections_user_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    db_type: Mapped[str] = mapped_column(String(32))
    url_encrypted: Mapped[str] = mapped_column(Text)
    dbname: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    annotations: Mapped[list["SchemaAnnotation"]] = relationship(
        back_populates="connection", cascade="all, delete-orphan", passive_deletes=True
    )


class SchemaAnnotation(Base):
    """A comment a user attaches by hand to a table or column of a target database.

    Kept out of the introspected schema cache because these are cheap to write and must take effect
    on the very next request, with no reconnect. column_name='' means the comment is on the table
    itself — empty string rather than NULL so the unique constraint actually catches duplicates
    (NULLs never compare equal in postgres).
    """

    __tablename__ = "schema_annotations"
    __table_args__ = (
        # named explicitly rather than by convention: the convention derives names from the first
        # column only, which would be misleading for a four-column key
        UniqueConstraint(
            "connection_id", "schema_name", "table_name", "column_name", name="uq_schema_annotations_key"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("saved_connections.id", ondelete="CASCADE"), index=True
    )
    schema_name: Mapped[str] = mapped_column(String(255), server_default="")
    table_name: Mapped[str] = mapped_column(String(255))
    column_name: Mapped[str] = mapped_column(String(255), server_default="")
    comment: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    connection: Mapped["SavedConnection"] = relationship(back_populates="annotations")
