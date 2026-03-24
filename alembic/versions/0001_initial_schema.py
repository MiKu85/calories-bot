"""initial schema

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2025-11-01 12:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a1b2c3d4e5f6'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('telegram_username', sa.String(length=64), nullable=True),
        sa.Column('preferred_name', sa.String(length=64), nullable=True),
        sa.Column('sex', sa.Enum('male', 'female', name='sex'), nullable=True),
        sa.Column('age', sa.Integer(), nullable=True),
        sa.Column('height_cm', sa.Float(), nullable=True),
        sa.Column('weight_kg', sa.Float(), nullable=True),
        sa.Column(
            'activity_level',
            sa.Enum('sedentary', 'light', 'moderate', 'active', 'very_active', name='activitylevel'),
            nullable=True,
        ),
        sa.Column('workouts_per_week', sa.Integer(), nullable=True),
        sa.Column('goal', sa.Enum('lose', 'maintain', 'gain', name='goal'), nullable=True),
        sa.Column('daily_calories_target', sa.Float(), nullable=True),
        sa.Column('daily_protein_g_target', sa.Float(), nullable=True),
        sa.Column('daily_fat_g_target', sa.Float(), nullable=True),
        sa.Column('daily_carbs_g_target', sa.Float(), nullable=True),
        sa.Column(
            'onboarding_state',
            sa.Enum(
                'new', 'awaiting_name', 'awaiting_sex', 'awaiting_age',
                'awaiting_height', 'awaiting_weight', 'awaiting_activity',
                'awaiting_workouts', 'awaiting_goal', 'completed',
                name='onboardingstate',
            ),
            nullable=False,
        ),
        sa.Column('is_subscribed', sa.Boolean(), nullable=False),
        sa.Column('onboarding_completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('first_meal_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('feedback_sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('telegram_id'),
    )
    op.create_index(op.f('ix_users_telegram_id'), 'users', ['telegram_id'], unique=True)

    op.create_table(
        'meals',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column(
            'input_type',
            sa.Enum('text', 'voice', 'photo', name='mealinputtype'),
            nullable=False,
        ),
        sa.Column('raw_input', sa.Text(), nullable=True),
        sa.Column('calories', sa.Float(), nullable=False),
        sa.Column('protein_g', sa.Float(), nullable=False),
        sa.Column('fat_g', sa.Float(), nullable=False),
        sa.Column('carbs_g', sa.Float(), nullable=False),
        sa.Column(
            'confidence',
            sa.Enum('high', 'medium', 'low', name='confidencelevel'),
            nullable=False,
        ),
        sa.Column('confidence_notes', sa.Text(), nullable=True),
        sa.Column('meal_items', sa.JSON(), nullable=True),
        sa.Column('is_confirmed', sa.Boolean(), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('logged_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_meals_user_id'), 'meals', ['user_id'], unique=False)

    op.create_table(
        'daily_aggregates',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('total_calories', sa.Float(), nullable=False),
        sa.Column('total_protein_g', sa.Float(), nullable=False),
        sa.Column('total_fat_g', sa.Float(), nullable=False),
        sa.Column('total_carbs_g', sa.Float(), nullable=False),
        sa.Column('meals_count', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'date', name='uq_daily_aggregate_user_date'),
    )
    op.create_index(op.f('ix_daily_aggregates_user_id'), 'daily_aggregates', ['user_id'], unique=False)

    op.create_table(
        'feedback_records',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('feedback_text', sa.Text(), nullable=True),
        sa.Column('has_voice_comment', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
    )

    op.create_table(
        'event_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column(
            'event_type',
            sa.Enum(
                'onboarding_completed', 'meal_logged', 'meal_corrected',
                'feedback_sent', 'error', 'subscription_check',
                name='eventtype',
            ),
            nullable=False,
        ),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_event_logs_event_type'), 'event_logs', ['event_type'], unique=False)
    op.create_index(op.f('ix_event_logs_user_id'), 'event_logs', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_event_logs_user_id'), table_name='event_logs')
    op.drop_index(op.f('ix_event_logs_event_type'), table_name='event_logs')
    op.drop_table('event_logs')

    op.drop_table('feedback_records')

    op.drop_index(op.f('ix_daily_aggregates_user_id'), table_name='daily_aggregates')
    op.drop_table('daily_aggregates')

    op.drop_index(op.f('ix_meals_user_id'), table_name='meals')
    op.drop_table('meals')

    op.drop_index(op.f('ix_users_telegram_id'), table_name='users')
    op.drop_table('users')

    sa.Enum(name='eventtype').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='confidencelevel').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='mealinputtype').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='onboardingstate').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='goal').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='activitylevel').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='sex').drop(op.get_bind(), checkfirst=True)
