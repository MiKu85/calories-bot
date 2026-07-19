"""
Tests for saved-meal templates («Мои блюда»):
- create_from_meal copies totals + items
- to_analysis_result rebuilds a high-confidence result usable by save_meal
- add-to-diary path creates a Meal and moves the daily aggregate
- name_exists is case-insensitive
"""
from __future__ import annotations

import pytest

from bot.db.models import ConfidenceLevel, Meal, MealInputType, OnboardingState, User
from bot.services import meal_service, saved_meal_service


async def _make_user(db) -> User:
    user = User(telegram_id=777_000_001, onboarding_state=OnboardingState.completed)
    db.add(user)
    await db.flush()
    return user


async def _make_meal(db, user: User) -> Meal:
    meal = Meal(
        user_id=user.id,
        input_type=MealInputType.text,
        raw_input="протеиновый коктейль",
        calories=320.0,
        protein_g=30.0,
        fat_g=8.0,
        carbs_g=25.0,
        confidence=ConfidenceLevel.high,
        meal_items=[
            {"name": "Протеин", "portion_description": "30 г",
             "calories": 120.0, "protein_g": 24.0, "fat_g": 2.0, "carbs_g": 3.0},
            {"name": "Ягоды", "portion_description": "100 г",
             "calories": 50.0, "protein_g": 1.0, "fat_g": 0.0, "carbs_g": 12.0},
        ],
        is_confirmed=True,
    )
    db.add(meal)
    await db.flush()
    return meal


class TestCreateAndRebuild:
    @pytest.mark.asyncio
    async def test_create_from_meal_copies_nutrition(self, db):
        user = await _make_user(db)
        meal = await _make_meal(db, user)

        saved = await saved_meal_service.create_from_meal(user, meal, "  Коктейль Мегана ", db)

        assert saved.id is not None
        assert saved.name == "Коктейль Мегана"  # trimmed
        assert saved.calories == 320.0
        assert saved.protein_g == 30.0
        assert len(saved.meal_items) == 2

    @pytest.mark.asyncio
    async def test_to_analysis_result_is_high_confidence(self, db):
        user = await _make_user(db)
        meal = await _make_meal(db, user)
        saved = await saved_meal_service.create_from_meal(user, meal, "Коктейль", db)

        result = saved_meal_service.to_analysis_result(saved)

        assert result.confidence == ConfidenceLevel.high
        assert result.needs_clarification is False
        assert result.total_calories == 320.0
        assert len(result.items) == 2
        assert result.items[0].name == "Протеин"

    @pytest.mark.asyncio
    async def test_add_to_diary_creates_meal_and_moves_aggregate(self, db):
        user = await _make_user(db)
        meal = await _make_meal(db, user)
        saved = await saved_meal_service.create_from_meal(user, meal, "Коктейль", db)

        result = saved_meal_service.to_analysis_result(saved)
        new_meal = await meal_service.save_meal(
            user=user,
            input_type=MealInputType.saved,
            raw_input=saved.name,
            result=result,
            db=db,
        )

        assert new_meal.id is not None
        assert new_meal.input_type == MealInputType.saved
        assert new_meal.calories == 320.0
        agg = await meal_service.get_today_aggregate(user.id, db)
        assert agg.total_calories >= 320.0


class TestNameExists:
    @pytest.mark.asyncio
    async def test_case_insensitive(self, db):
        user = await _make_user(db)
        meal = await _make_meal(db, user)
        await saved_meal_service.create_from_meal(user, meal, "Коктейль", db)

        assert await saved_meal_service.name_exists(user.id, "коктейль", db) is True
        assert await saved_meal_service.name_exists(user.id, "  КОКТЕЙЛЬ ", db) is True
        assert await saved_meal_service.name_exists(user.id, "Омлет", db) is False
