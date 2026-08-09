"""message reasoning

Revision ID: 3c27ac5985c8
Revises: 65782ea3b537
Create Date: 2026-08-09 12:25:02.779394

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3c27ac5985c8'
down_revision: Union[str, Sequence[str], None] = '65782ea3b537'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable with no default, same reasoning as result_data: every existing message predates the
    # verifier and genuinely has no review behind it — NULL says that correctly, a backfill would
    # have to invent one.
    op.add_column("messages", sa.Column("reasoning", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "reasoning")
