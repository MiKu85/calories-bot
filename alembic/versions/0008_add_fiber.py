"""add fiber tracking: meals.fiber_g, saved_meals.fiber_g, daily_aggregates.total_fiber_g, users.daily_fiber_g_target

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-20 10:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b8c9d0e1f2a3'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows get 0 g fiber (feature launched now; past meals not re-estimated).
    op.add_column('meals', sa.Column('fiber_g', sa.Float(), nullable=False, server_default='0'))
    op.add_column('saved_meals', sa.Column('fiber_g', sa.Float(), nullable=False, server_default='0'))
    op.add_column('daily_aggregates', sa.Column('total_fiber_g', sa.Float(), nullable=False, server_default='0'))
    # Target is nullable — filled on next target recalculation (by sex).
    op.add_column('users', sa.Column('daily_fiber_g_target', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'daily_fiber_g_target')
    op.drop_column('daily_aggregates', 'total_fiber_g')
    op.drop_column('saved_meals', 'fiber_g')
    op.drop_column('meals', 'fiber_g')
