"""
Tests for stats_service edge cases:
- empty day (no meals yet)
- over-target display
- missing targets (incomplete profile)
- remaining display helpers
"""
from __future__ import annotations

from datetime import date

import pytest

from bot.services.stats_service import (
    _remaining_str,
    format_stats,
    get_status_phrase,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_agg(calories=0.0, protein=0.0, fat=0.0, carbs=0.0, meals=0):
    from bot.db.models import DailyAggregate
    agg = DailyAggregate.__new__(DailyAggregate)
    agg.total_calories = calories
    agg.total_protein_g = protein
    agg.total_fat_g = fat
    agg.total_carbs_g = carbs
    agg.meals_count = meals
    agg.date = date.today()
    return agg


def _make_user(targets=True):
    from bot.db.models import User
    u = User.__new__(User)
    if targets:
        u.daily_calories_target = 2000.0
        u.daily_protein_g_target = 120.0
        u.daily_fat_g_target = 55.0
        u.daily_carbs_g_target = 240.0
    else:
        u.daily_calories_target = None
        u.daily_protein_g_target = None
        u.daily_fat_g_target = None
        u.daily_carbs_g_target = None
    return u


# ── _remaining_str ─────────────────────────────────────────────────────────────

class TestRemainingStr:
    def test_positive_remaining(self):
        result = _remaining_str(800, 2000, "ккал")
        assert "осталось" in result
        assert "1200" in result

    def test_zero_remaining(self):
        result = _remaining_str(2000, 2000, "ккал")
        assert "осталось" in result
        assert "0" in result

    def test_over_target_shows_pereboy(self):
        result = _remaining_str(2300, 2000, "ккал")
        assert "перебор" in result
        assert "300" in result
        # Should NOT show negative number
        assert "-" not in result


# ── format_stats: empty day ───────────────────────────────────────────────────

class TestFormatStatsEmptyDay:
    def test_empty_day_shows_no_meals_message(self):
        text = format_stats(_make_agg(meals=0), _make_user())
        assert "нет" in text.lower() or "пока" in text.lower()

    def test_empty_day_shows_target(self):
        text = format_stats(_make_agg(meals=0), _make_user())
        assert "2000" in text

    def test_empty_day_prompts_to_log(self):
        text = format_stats(_make_agg(meals=0), _make_user())
        # Should invite user to start logging
        assert any(w in text.lower() for w in ["напиши", "текст", "фото"])


# ── format_stats: over target ─────────────────────────────────────────────────

class TestFormatStatsOverTarget:
    def test_over_target_shows_pereboy(self):
        agg = _make_agg(calories=2400, protein=130, fat=60, carbs=260, meals=4)
        text = format_stats(agg, _make_user())
        assert "перебор" in text

    def test_over_target_no_negative_numbers_for_calories(self):
        agg = _make_agg(calories=2400, protein=130, fat=60, carbs=260, meals=4)
        text = format_stats(agg, _make_user())
        # Remaining should not show raw "-400"
        assert "-400" not in text

    def test_status_phrase_above_target(self):
        agg = _make_agg(calories=2400, protein=130, fat=60, carbs=260, meals=4)
        text = format_stats(agg, _make_user())
        # Should mention being above plan
        assert any(w in text.lower() for w in ["выше", "превышение"])


# ── format_stats: no targets (incomplete profile) ─────────────────────────────

class TestFormatStatsNoTargets:
    def test_shows_consumed_without_target(self):
        agg = _make_agg(calories=1500, protein=80, fat=45, carbs=180, meals=3)
        user = _make_user(targets=False)
        # Need to set targets_set to return False
        user.daily_calories_target = None
        text = format_stats(agg, user)
        assert "1500" in text

    def test_suggests_completing_profile(self):
        agg = _make_agg(calories=1500, protein=80, fat=45, carbs=180, meals=3)
        user = _make_user(targets=False)
        user.daily_calories_target = None
        text = format_stats(agg, user)
        assert "profile" in text.lower() or "/profile" in text


# ── format_stats: normal day ──────────────────────────────────────────────────

class TestFormatStatsNormalDay:
    def test_shows_meals_count(self):
        agg = _make_agg(calories=1200, protein=70, fat=40, carbs=150, meals=3)
        text = format_stats(agg, _make_user())
        assert "3" in text

    def test_shows_calories_consumed(self):
        agg = _make_agg(calories=1200, protein=70, fat=40, carbs=150, meals=2)
        text = format_stats(agg, _make_user())
        assert "1200" in text

    def test_shows_target(self):
        agg = _make_agg(calories=1200, protein=70, fat=40, carbs=150, meals=2)
        text = format_stats(agg, _make_user())
        assert "2000" in text

    def test_on_plan_status(self):
        # 90% of target → "по плану"
        agg = _make_agg(calories=1800, protein=100, fat=50, carbs=210, meals=3)
        text = format_stats(agg, _make_user())
        assert "план" in text.lower()


# ── get_status_phrase edge cases ──────────────────────────────────────────────

class TestGetStatusPhraseEdgeCases:
    def test_exactly_100_percent_is_on_plan(self):
        phrase = get_status_phrase(2000, 2000)
        assert "план" in phrase.lower()

    def test_exactly_85_percent_boundary(self):
        # 85% is the boundary between "запас" and "план"
        phrase = get_status_phrase(1700, 2000)  # 85%
        assert phrase  # should return something

    def test_very_high_over_target(self):
        phrase = get_status_phrase(4000, 2000)  # 200%
        assert "превышение" in phrase.lower()
