"""add deleted_at and replaced_by_meal_id to meals

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-16 16:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'meals',
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'meals',
        sa.Column('replaced_by_meal_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_meals_replaced_by',
        'meals', 'meals',
        ['replaced_by_meal_id'], ['id'],
    )
    op.create_index('ix_meals_deleted_at', 'meals', ['deleted_at'])


def downgrade() -> None:
    op.drop_index('ix_meals_deleted_at', table_name='meals')
    op.drop_constraint('fk_meals_replaced_by', 'meals', type_='foreignkey')
    op.drop_column('meals', 'replaced_by_meal_id')
    op.drop_column('meals', 'deleted_at')
