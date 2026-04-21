"""add meal_type to meals

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-21 12:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    meal_type_enum = sa.Enum(
        'breakfast', 'lunch', 'snack', 'dinner',
        name='mealtype',
    )
    meal_type_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        'meals',
        sa.Column('meal_type', meal_type_enum, nullable=True),
    )


def downgrade() -> None:
    op.drop_column('meals', 'meal_type')
    sa.Enum(name='mealtype').drop(op.get_bind(), checkfirst=True)
