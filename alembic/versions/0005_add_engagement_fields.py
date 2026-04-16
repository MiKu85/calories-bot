"""add tips_meal_counter and tip_history to users

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-16 18:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'tips_meal_counter',
            sa.Integer(),
            nullable=False,
            server_default='0',
        ),
    )
    op.add_column(
        'users',
        sa.Column('tip_history', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('users', 'tip_history')
    op.drop_column('users', 'tips_meal_counter')
