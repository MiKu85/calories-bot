"""
Tests for meal pipeline:
- MealAnalysisResult validation (pure, no DB)
- stats_service formatting (pure, no DB)
- meal_service save + aggregate (requires DB via conftest)
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from bot.ai.schemas import ConfidenceLevel, MealAnalysisResult, MealItem
from bot.services.stats_service import (
    format_stats,
    format_meal_result,
    get_status_phrase,
)


# ── MealAnalysisResult validation ─────────────────────────────────────────────

class TestMealAnalysisResultValidation:
    def _valid_payload(self, **overrides) -> dict:
        base = {
            "items": [
                {
                    "name": "Куриная грудка",
                    "portion_description": "200г",
                    "calories": 220,
                    "protein_g": 42,
                    "fat_g": 4,
                    "carbs_g": 0,
                }
            ],
            "total_calories": 220,
            "total_protein_g": 42,
            "total_fat_g": 4,
            "total_carbs_g": 0,
            "confidence": "high",
            "confidence_notes": None,
            "needs_clarification": False,
            "clarification_prompt": None,
        }
        base.update(overrides)
        return base

    def test_valid_high_confidence(self):
        result = MealAnalysisResult.model_validate(self._valid_payload())
        assert result.confidence == ConfidenceLevel.high
        assert result.needs_clarification is False
        assert len(result.items) == 1

    def test_low_confidence_auto_fills_prompt(self):
        result = MealAnalysisResult.model_validate(
            self._valid_payload(
                confidence="low",
                needs_clarification=True,
                clarification_prompt=None,
            )
        )
        # validator should fill in a default clarification prompt
        assert result.clarification_prompt is not None
        assert len(result.clarification_prompt) > 0

    def test_explicit_clarification_prompt_preserved(self):
        prompt = "Уточни, пожалуйста, размер порции."
        result = MealAnalysisResult.model_validate(
            self._valid_payload(
                confidence="low",
                needs_clarification=True,
                clarification_prompt=prompt,
            )
        )
        assert result.clarification_prompt == prompt

    def test_negative_calories_rejected(self):
        with pytest.raises(ValidationError):
            MealAnalysisResult.model_validate(self._valid_payload(total_calories=-10))

    def test_negative_macros_rejected(self):
        with pytest.raises(ValidationError):
            MealAnalysisResult.model_validate(self._valid_payload(total_protein_g=-1))

    def test_empty_items_allowed(self):
        result = MealAnalysisResult.model_validate(
            self._valid_payload(items=[], needs_clarification=True)
        )
        assert result.items == []

    def test_multiple_items(self):
        payload = self._valid_payload()
        payload["items"].append({
            "name": "Рис",
            "portion_description": "150г",
            "calories": 175,
            "protein_g": 3.5,
            "fat_g": 0.3,
            "carbs_g": 38,
        })
        result = MealAnalysisResult.model_validate(payload)
        assert len(result.items) == 2


# ── Status phrase ──────────────────────────────────────────────────────────────

class TestGetStatusPhrase:
    def test_below_85_percent(self):
        phrase = get_status_phrase(consumed_calories=1000, target_calories=2000)
        assert "запас" in phrase.lower()

    def test_on_plan_90_percent(self):
        phrase = get_status_phrase(consumed_calories=1800, target_calories=2000)
        assert "план" in phrase.lower()

    def test_slight_over_110_percent(self):
        phrase = get_status_phrase(consumed_calories=2200, target_calories=2000)
        assert "выше" in phrase.lower() or "превышение" in phrase.lower()

    def test_zero_target_returns_empty(self):
        phrase = get_status_phrase(consumed_calories=1000, target_calories=0)
        assert phrase == ""


# ── format_meal_result ─────────────────────────────────────────────────────────

class TestFormatMealResult:
    def _make_agg(self):
        from bot.db.models import DailyAggregate
        from datetime import date
        agg = DailyAggregate.__new__(DailyAggregate)
        agg.total_calories = 800.0
        agg.total_protein_g = 55.0
        agg.total_fat_g = 22.0
        agg.total_carbs_g = 90.0
        agg.meals_count = 2
        agg.date = date.today()
        return agg

    def _make_user(self):
        from bot.db.models import User
        u = User.__new__(User)
        u.daily_calories_target = 2300.0
        u.daily_protein_g_target = 135.0
        u.daily_fat_g_target = 60.0
        u.daily_carbs_g_target = 260.0
        return u

    def test_contains_meal_calories(self):
        text = format_meal_result(
            meal_calories=390,
            meal_protein=18,
            meal_fat=22,
            meal_carbs=28,
            meal_items=None,
            agg=self._make_agg(),
            user=self._make_user(),
        )
        assert "390" in text

    def test_contains_daily_totals(self):
        text = format_meal_result(
            meal_calories=390,
            meal_protein=18,
            meal_fat=22,
            meal_carbs=28,
            meal_items=None,
            agg=self._make_agg(),
            user=self._make_user(),
        )
        assert "800" in text  # agg.total_calories

    def test_items_breakdown_shown(self):
        items = [
            {"name": "Яйца", "portion_description": "2 шт", "calories": 140,
             "protein_g": 12, "fat_g": 10, "carbs_g": 0},
        ]
        text = format_meal_result(390, 18, 22, 28, items, self._make_agg(), self._make_user())
        assert "Яйца" in text

    def test_remaining_shown_when_targets_set(self):
        text = format_meal_result(390, 18, 22, 28, None, self._make_agg(), self._make_user())
        assert "Осталось" in text


# ── DB integration tests (require TEST_DATABASE_URL) ──────────────────────────

@pytest.mark.asyncio
async def test_save_and_get_aggregate(db):
    """Save a meal and verify daily aggregate is updated."""
    from datetime import datetime, timezone
    from bot.db.models import ActivityLevel, Goal, OnboardingState, Sex, User
    from bot.ai.schemas import ConfidenceLevel, MealAnalysisResult, MealItem
    from bot.db.models import MealInputType
    from bot.services.meal_service import save_meal, get_today_aggregate

    # Create a test user
    user = User(
        telegram_id=999_000_001,
        onboarding_state=OnboardingState.completed,
        sex=Sex.male,
        age=30,
        height_cm=175,
        weight_kg=75,
        activity_level=ActivityLevel.moderate,
        goal=Goal.maintain,
        daily_calories_target=2300,
        daily_protein_g_target=120,
        daily_fat_g_target=60,
        daily_carbs_g_target=270,
    )
    db.add(user)
    await db.flush()

    result = MealAnalysisResult(
        items=[MealItem(name="Овсянка", portion_description="200г", calories=180,
                        protein_g=6, fat_g=3, carbs_g=32)],
        total_calories=180,
        total_protein_g=6,
        total_fat_g=3,
        total_carbs_g=32,
        confidence=ConfidenceLevel.high,
    )

    meal = await save_meal(user, MealInputType.text, "Овсянка 200г", result, db)
    assert meal.id is not None
    assert meal.calories == 180

    agg = await get_today_aggregate(user.id, db)
    assert agg.total_calories == pytest.approx(180)
    assert agg.meals_count == 1
    assert user.first_meal_at is not None


@pytest.mark.asyncio
async def test_delete_meal_updates_aggregate(db):
    """Deleting a meal should subtract its values from the aggregate."""
    from bot.db.models import ActivityLevel, Goal, OnboardingState, Sex, User, MealInputType
    from bot.ai.schemas import ConfidenceLevel, MealAnalysisResult, MealItem
    from bot.services.meal_service import save_meal, delete_meal, get_today_aggregate

    user = User(
        telegram_id=999_000_002,
        onboarding_state=OnboardingState.completed,
        daily_calories_target=2000,
        daily_protein_g_target=100,
        daily_fat_g_target=55,
        daily_carbs_g_target=230,
    )
    db.add(user)
    await db.flush()

    result = MealAnalysisResult(
        items=[],
        total_calories=500,
        total_protein_g=30,
        total_fat_g=15,
        total_carbs_g=60,
        confidence=ConfidenceLevel.medium,
    )

    meal = await save_meal(user, MealInputType.text, "Обед", result, db)
    await delete_meal(meal, db)

    agg = await get_today_aggregate(user.id, db)
    assert agg.total_calories == pytest.approx(0)
    assert agg.meals_count == 0
