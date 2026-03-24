"""
User service — profile updates and target management.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import (
    ActivityLevel,
    Goal,
    OnboardingState,
    Sex,
    User,
)
from bot.services.target_calculator import UserTargets, calculate_targets


async def apply_targets(user: User, db: AsyncSession) -> UserTargets:
    """Recalculate and persist daily targets. Requires complete profile."""
    if not user.profile_complete or user.activity_level is None or user.goal is None:
        raise ValueError("Cannot calculate targets: profile is incomplete")

    targets = calculate_targets(
        sex=user.sex,
        weight_kg=user.weight_kg,
        height_cm=user.height_cm,
        age=user.age,
        activity_level=user.activity_level,
        goal=user.goal,
    )

    user.daily_calories_target = targets.daily_calories
    user.daily_protein_g_target = targets.daily_protein_g
    user.daily_fat_g_target = targets.daily_fat_g
    user.daily_carbs_g_target = targets.daily_carbs_g
    await db.flush()
    return targets


async def reset_onboarding(user: User, db: AsyncSession) -> None:
    """Clear profile and reset to onboarding start."""
    user.preferred_name = None
    user.sex = None
    user.age = None
    user.height_cm = None
    user.weight_kg = None
    user.activity_level = None
    user.workouts_per_week = None
    user.goal = None
    user.daily_calories_target = None
    user.daily_protein_g_target = None
    user.daily_fat_g_target = None
    user.daily_carbs_g_target = None
    user.onboarding_state = OnboardingState.new
    user.onboarding_completed_at = None
    await db.flush()
