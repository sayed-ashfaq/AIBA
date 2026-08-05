"""connections owned by users

Revision ID: acc88355530e
Revises: 70db931a80cf
Create Date: 2026-07-22 15:28:26.278351

Makes a saved connection private to the account that created it, and gives each user a persistent
pointer at whichever one they last activated.

The upgrade is add-nullable / backfill / set-not-null rather than a straight NOT NULL column,
because connections created before this migration have no owner. They are adopted by the oldest
account that isn't obviously a test fixture; if the database has no users at all, they are dropped,
since under the new model an ownerless row is unreachable by anybody.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'acc88355530e'
down_revision: Union[str, Sequence[str], None] = '70db931a80cf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- saved_connections.user_id ---
    op.add_column('saved_connections', sa.Column('user_id', sa.UUID(), nullable=True))

    # adopt pre-ownership rows. Resolved by query rather than a hardcoded id so this migration is
    # reproducible on any database; excludes the test.user.*@example.com fixtures so real
    # connections don't end up attached to a throwaway account.
    op.execute(
        """
        UPDATE saved_connections SET user_id = (
            SELECT id FROM users
            WHERE email NOT LIKE 'test.user.%@example.com'
            ORDER BY created_at
            LIMIT 1
        )
        WHERE user_id IS NULL
        """
    )
    # nothing to adopt them: no user exists, so nobody could ever reach these rows again
    op.execute("DELETE FROM saved_connections WHERE user_id IS NULL")

    op.alter_column('saved_connections', 'user_id', nullable=False)

    op.drop_constraint(op.f('uq_saved_connections_name'), 'saved_connections', type_='unique')
    op.create_index(op.f('ix_saved_connections_user_id'), 'saved_connections', ['user_id'], unique=False)
    op.create_unique_constraint('uq_saved_connections_user_name', 'saved_connections', ['user_id', 'name'])
    op.create_foreign_key(op.f('fk_saved_connections_user_id_users'), 'saved_connections', 'users', ['user_id'], ['id'], ondelete='CASCADE')

    # --- users.active_connection_id ---
    op.add_column('users', sa.Column('active_connection_id', sa.UUID(), nullable=True))
    op.create_foreign_key('fk_users_active_connection_id_saved_connections', 'users', 'saved_connections', ['active_connection_id'], ['id'], ondelete='SET NULL', use_alter=True)


def downgrade() -> None:
    op.drop_constraint('fk_users_active_connection_id_saved_connections', 'users', type_='foreignkey')
    op.drop_column('users', 'active_connection_id')
    op.drop_constraint(op.f('fk_saved_connections_user_id_users'), 'saved_connections', type_='foreignkey')
    op.drop_constraint('uq_saved_connections_user_name', 'saved_connections', type_='unique')
    op.drop_index(op.f('ix_saved_connections_user_id'), table_name='saved_connections')
    # the old constraint was unique on name alone, so per-user duplicates must go first or the
    # constraint can't be recreated
    op.execute(
        """
        DELETE FROM saved_connections a USING saved_connections b
        WHERE a.name = b.name AND a.created_at > b.created_at
        """
    )
    op.create_unique_constraint(op.f('uq_saved_connections_name'), 'saved_connections', ['name'], postgresql_nulls_not_distinct=False)
    op.drop_column('saved_connections', 'user_id')
