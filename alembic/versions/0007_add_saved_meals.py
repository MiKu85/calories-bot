"""add saved_meals (user meal templates) + 'saved' meal input type

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-18 10:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a7b8c9d0e1f2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # New enum value for meals added from a saved template.
    # ADD VALUE is allowed inside a transaction on PG12+; the value is not USED
    # in this same migration, so no separate autocommit block is required.
    op.execute("ALTER TYPE mealinputtype ADD VALUE IF NOT EXISTS 'saved'")

    op.create_table(
        'saved_meals',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('calories', sa.Float(), nullable=False),
        sa.Column('protein_g', sa.Float(), nullable=False),
        sa.Column('fat_g', sa.Float(), nullable=False),
        sa.Column('carbs_g', sa.Float(), nullable=False),
        sa.Column('meal_items', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('user_id', 'name', name='uq_saved_meal_user_name'),
    )
    op.create_index('ix_saved_meals_user_id', 'saved_meals', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_saved_meals_user_id', table_name='saved_meals')
    op.drop_table('saved_meals')
    # NB: PostgreSQL cannot DROP a single enum value; 'saved' stays on mealinputtype.
