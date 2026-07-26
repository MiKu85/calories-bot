"""
Tests for fiber tracking:
- fiber target by sex (women 25, men 30, unknown → 25)
- calculate_targets carries daily_fiber_g
- daily aggregate sums fiber across meals
- format_stats / format_meal_result render fiber
"""
from __future__ import annotations

import pytest

from bot.db.models import (
    ActivityLevel, ConfidenceLevel, DailyAggregate, Goal,
    Meal, MealInputType, OnboardingState, Sex, User,
)
from bot.services import meal_service
from bot.services.stats_service import format_meal_result, format_stats
from bot.services.target_calculator import (
    FIBER_TARGET_FEMALE, FIBER_TARGET_MALE, calculate_targets, fiber_target_for,
)


class TestFiberTarget:
    def test_by_sex(self):
        assert fiber_target_for(Sex.female) == FIBER_TARGET_FEMALE == 25.0
        assert fiber_target_for(Sex.male) == FIBER_TARGET_MALE == 30.0

    def test_unknown_defaults_to_min(self):
        assert fiber_target_for(None) == 25.0

    def test_calculate_targets_includes_fiber(self):
        t_f = calculate_targets(Sex.female, 60, 165, 30, ActivityLevel.moderate, Goal.maintain)
        t_m = calculate_targets(Sex.male, 80, 180, 30, ActivityLevel.moderate, Goal.maintain)
        assert t_f.daily_fiber_g == 25.0
        assert t_m.daily_fiber_g == 30.0


class TestFiberAggregate:
    @pytest.mark.asyncio
    async def test_daily_aggregate_sums_fiber(self, db):
        user = User(telegram_id=555_000_101, onboarding_state=OnboardingState.completed)
        db.add(user)
        await db.flush()
        for fiber in (4.0, 6.5):
            db.add(Meal(
                user_id=user.id, input_type=MealInputType.text, raw_input="x",
                calories=100, protein_g=5, fat_g=2, carbs_g=20, fiber_g=fiber,
                confidence=ConfidenceLevel.high, is_confirmed=True,
            ))
        await db.flush()
        from datetime import datetime, timezone
        agg = await meal_service.recalculate_daily_aggregate(
            user.id, datetime.now(timezone.utc).date(), db
        )
        assert agg.total_fiber_g == pytest.approx(10.5)


class TestFiberDisplay:
    def _user(self) -> User:
        return User(
            telegram_id=555_000_202, sex=Sex.female,
            onboarding_state=OnboardingState.completed,
            daily_calories_target=1800, daily_protein_g_target=90,
            daily_fat_g_target=60, daily_carbs_g_target=200,
            daily_fiber_g_target=25,
        )

    def _agg(self) -> DailyAggregate:
        from datetime import date as _date
        return DailyAggregate(
            user_id=1, date=_date.today(),
            total_calories=500, total_protein_g=30, total_fat_g=15,
            total_carbs_g=60, total_fiber_g=8, meals_count=1,
        )

    def test_stats_shows_fiber_row(self):
        out = format_stats(self._agg(), self._user())
        assert "Клетчатка" in out
        assert "25" in out  # target

    def test_meal_result_shows_fiber(self):
        out = format_meal_result(
            meal_calories=200, meal_protein=10, meal_fat=5, meal_carbs=25,
            meal_items=[{"name": "Салат", "portion_description": "150г",
                         "calories": 200, "protein_g": 10, "fat_g": 5,
                         "carbs_g": 25, "fiber_g": 6}],
            agg=self._agg(), user=self._user(), meal_fiber=6,
        )
        assert "Кл" in out  # fiber label present in item + totals
