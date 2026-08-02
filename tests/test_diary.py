"""
Tests for the per-day diary screen (bot/handlers/diary.py).

Context: users could only fix the most recent meal — the buttons under an older
result message are gone once the chat scrolls. Wrong entries from earlier days
stayed in the diary forever and kept showing up in the weekly export.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.db.models import ConfidenceLevel, Meal, MealInputType, OnboardingState, User
from bot.handlers.diary import render_day
from bot.services.meal_service import delete_meal


async def _make_user(db) -> User:
    user = User(
        telegram_id=777_000_099,
        onboarding_state=OnboardingState.completed,
        daily_calories_target=1736.0,
        daily_protein_g_target=111.0,
        daily_fat_g_target=49.0,
        daily_carbs_g_target=210.0,
        daily_fiber_g_target=25.0,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_meal(db, user: User, calories: float, days_ago: int, name: str) -> Meal:
    meal = Meal(
        user_id=user.id,
        input_type=MealInputType.photo,
        raw_input=None,
        calories=calories,
        protein_g=10.0,
        fat_g=5.0,
        carbs_g=20.0,
        fiber_g=2.0,
        confidence=ConfidenceLevel.medium,
        meal_items=[{"name": name, "portion_description": "100 г",
                     "calories": calories, "protein_g": 10.0, "fat_g": 5.0,
                     "carbs_g": 20.0, "fiber_g": 2.0}],
        logged_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    db.add(meal)
    await db.flush()
    return meal


class TestRenderDay:
    @pytest.mark.asyncio
    async def test_today_lists_meals_and_total(self, db):
        user = await _make_user(db)
        await _make_meal(db, user, 460.0, 0, "Лаваш")
        await _make_meal(db, user, 353.0, 0, "Йогурт")

        text, kb = await render_day(user, db, datetime.now(timezone.utc).date())

        assert "Лаваш" in text and "Йогурт" in text
        assert "813" in text  # 460 + 353
        assert kb.inline_keyboard  # fix/delete buttons present

    @pytest.mark.asyncio
    async def test_past_day_is_reachable(self, db):
        """The whole point: reach and edit an older day, not just today."""
        user = await _make_user(db)
        past = datetime.now(timezone.utc).date() - timedelta(days=5)
        await _make_meal(db, user, 500.0, 5, "Каша")

        text, kb = await render_day(user, db, past)

        assert "Каша" in text
        assert "500" in text

    @pytest.mark.asyncio
    async def test_deleted_meal_is_excluded(self, db):
        user = await _make_user(db)
        keep = await _make_meal(db, user, 400.0, 0, "Суп")
        drop = await _make_meal(db, user, 900.0, 0, "Дубль")
        await delete_meal(drop, db)

        text, _ = await render_day(user, db, datetime.now(timezone.utc).date())

        assert "Суп" in text
        assert "Дубль" not in text
        assert "400" in text

    @pytest.mark.asyncio
    async def test_empty_day(self, db):
        user = await _make_user(db)
        text, kb = await render_day(user, db, datetime.now(timezone.utc).date())

        assert "приёмов пищи нет" in text

    @pytest.mark.asyncio
    async def test_overrun_shown_without_negative_number(self, db):
        user = await _make_user(db)
        await _make_meal(db, user, 2400.0, 0, "Много")

        text, _ = await render_day(user, db, datetime.now(timezone.utc).date())

        assert "перебор" in text
        assert "-" not in text.split("перебор")[1][:10]

    @pytest.mark.asyncio
    async def test_buttons_carry_meal_id_and_date(self, db):
        user = await _make_user(db)
        meal = await _make_meal(db, user, 300.0, 0, "Омлет")
        today = datetime.now(timezone.utc).date()

        _, kb = await render_day(user, db, today)
        payloads = [b.callback_data for row in kb.inline_keyboard for b in row]

        assert f"meal_fix_day:{meal.id}:{today.isoformat()}" in payloads
        assert f"meal_del_day:{meal.id}:{today.isoformat()}" in payloads

    @pytest.mark.asyncio
    async def test_time_shown_in_user_timezone(self, db):
        """Meals are stored in UTC; the user compares them with their phone clock."""
        user = await _make_user(db)
        user.timezone = "Europe/Moscow"
        meal = await _make_meal(db, user, 300.0, 0, "Омлет")
        meal.logged_at = datetime(2026, 7, 27, 6, 58, tzinfo=timezone.utc)
        await db.flush()

        text, _ = await render_day(user, db, meal.logged_at.date())

        assert "09:58" in text  # UTC+3
        assert "06:58" not in text

    @pytest.mark.asyncio
    async def test_today_has_no_next_day_button(self, db):
        user = await _make_user(db)
        await _make_meal(db, user, 300.0, 0, "Омлет")

        _, kb = await render_day(user, db, datetime.now(timezone.utc).date())
        payloads = [b.callback_data for row in kb.inline_keyboard for b in row]

        assert not any(p.startswith("day_nav:") and "следующий" in p for p in payloads)
        assert any(p.startswith("day_nav:") for p in payloads)  # prev day available
