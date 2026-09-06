"""user schema_mode

Revision ID: 5eace1c55654
Revises: 3c27ac5985c8
Create Date: 2026-09-06 15:24:24.669718

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5eace1c55654'
down_revision: Union[str, Sequence[str], None] = '3c27ac5985c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Per-user choice of how the SQL agent gets its schema: "plain" (the full flat schema, current
    # behaviour) or "graph" (the experimental schema_linking slice). NOT NULL with a server default
    # so every existing row is a well-defined "plain" without a backfill.
    op.add_column(
        "users",
        sa.Column("schema_mode", sa.String(length=16), nullable=False, server_default="plain"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "schema_mode")
