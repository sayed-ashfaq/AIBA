"""message result data

Revision ID: 65782ea3b537
Revises: 43001f0660c4
Create Date: 2026-07-29 15:51:31.077864

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '65782ea3b537'
down_revision: Union[str, Sequence[str], None] = '43001f0660c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable with no default: every existing message predates charting and genuinely has no rows
    # behind it, which is what NULL says. Backfilling would mean re-running old queries against
    # today's data — a different answer under the same prose.
    op.add_column("messages", sa.Column("result_data", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "result_data")
