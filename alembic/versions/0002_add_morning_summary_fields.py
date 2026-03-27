"""add morning summary fields to users

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2025-11-15 10:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'timezone',
            sa.String(length=64),
            nullable=False,
            server_default='Europe/Moscow',
        ),
    )
    op.add_column(
        'users',
        sa.Column('morning_sent_date', sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('users', 'morning_sent_date')
    op.drop_column('users', 'timezone')
