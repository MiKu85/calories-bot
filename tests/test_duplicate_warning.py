"""
Tests for duplicate-meal detection (build_duplicate_warning).

Regression context: photos are saved by meal_batch.flush_meal_buffer, which used
to skip the duplicate check entirely — a re-shot photo of the same meal was
silently logged as a second meal and inflated the daily total.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.db.models import ConfidenceLevel, Meal, MealInputType, OnboardingState, User
from bot.handlers.meal import build_duplicate_warning


async def _make_user(db) -> User:
    user = User(telegram_id=777_000_042, onboarding_state=OnboardingState.completed)
    db.add(user)
    await db.flush()
    return user


async def _make_meal(db, user: User, raw_input: str | None, minutes_ago: int = 2) -> Meal:
    meal = Meal(
        user_id=user.id,
        input_type=MealInputType.photo,
        raw_input=raw_input,
        calories=460.0,
        protein_g=24.0,
        fat_g=20.0,
        carbs_g=58.0,
        confidence=ConfidenceLevel.medium,
        meal_items=[],
        logged_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )
    db.add(meal)
    await db.flush()
    return meal


class TestDuplicateWarning:
    @pytest.mark.asyncio
    async def test_two_photos_without_caption_are_suspicious(self, db):
        """Marina's case: same breakfast re-shot two minutes later."""
        user = await _make_user(db)
        await _make_meal(db, user, raw_input=None, minutes_ago=2)
        new_meal = await _make_meal(db, user, raw_input=None, minutes_ago=0)

        warning = await build_duplicate_warning(
            user_id=user.id, new_meal_id=new_meal.id, raw_input=None, db=db
        )

        assert warning is not None
        text, kb = warning
        assert "не повтор" in text

    @pytest.mark.asyncio
    async def test_similar_captions_are_suspicious(self, db):
        user = await _make_user(db)
        await _make_meal(db, user, raw_input="лаваш с маслом и яйцом", minutes_ago=3)
        new_meal = await _make_meal(db, user, raw_input="лаваш с маслом и яйцом", minutes_ago=0)

        warning = await build_duplicate_warning(
            user_id=user.id,
            new_meal_id=new_meal.id,
            raw_input="лаваш с маслом и яйцом",
            db=db,
        )

        assert warning is not None

    @pytest.mark.asyncio
    async def test_different_text_meals_pass(self, db):
        user = await _make_user(db)
        await _make_meal(db, user, raw_input="овсянка с бананом", minutes_ago=3)
        new_meal = await _make_meal(db, user, raw_input="куриная грудка с рисом", minutes_ago=0)

        warning = await build_duplicate_warning(
            user_id=user.id, new_meal_id=new_meal.id, raw_input="куриная грудка с рисом", db=db
        )

        assert warning is None

    @pytest.mark.asyncio
    async def test_old_meal_outside_window_passes(self, db):
        """Same photo logged hours apart is a genuine second meal, not a repeat."""
        user = await _make_user(db)
        await _make_meal(db, user, raw_input=None, minutes_ago=180)
        new_meal = await _make_meal(db, user, raw_input=None, minutes_ago=0)

        warning = await build_duplicate_warning(
            user_id=user.id, new_meal_id=new_meal.id, raw_input=None, db=db
        )

        assert warning is None

    @pytest.mark.asyncio
    async def test_first_meal_of_day_passes(self, db):
        user = await _make_user(db)
        new_meal = await _make_meal(db, user, raw_input=None, minutes_ago=0)

        warning = await build_duplicate_warning(
            user_id=user.id, new_meal_id=new_meal.id, raw_input=None, db=db
        )

        assert warning is None
