"""message activity trail

Revision ID: 2d508bc55d56
Revises: 5eace1c55654
Create Date: 2026-09-09 16:15:59.875108

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = '2d508bc55d56'
down_revision: Union[str, Sequence[str], None] = '5eace1c55654'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # The streamed turn's activity trail — one entry per agent tool call (which agent, which tool,
    # the SQL, timing), already reduced to the render-ready shape the client shows. Persisted so a
    # reopened or refreshed conversation shows the same trail the live turn did, instead of it
    # vanishing with the browser tab. Nullable: only assistant turns from POST /chat/stream carry
    # one; every prior row and every plain POST /chat turn stays a well-defined NULL. JSONB, like
    # result_data, so "which turns ran execute_sql" stays answerable without a migration.
    op.add_column("messages", sa.Column("activity", JSONB(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "activity")
