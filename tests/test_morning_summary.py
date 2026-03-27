"""
Tests for morning_summary service.

Covers 8 scenarios:
  1. Fallback — no data logged yesterday (< 300 kcal)
  2. Good day — calories and protein on target
  3. Low calories — consumed < 70% of target
  4. High calories — consumed > 115% of target
  5. Low protein — calories OK but protein < 75% of target
  6. High fat — calories OK but fat > 120% of target
  7. Improving trend — yesterday closer to target than day before
  8. Stable good — two consecutive good days

All tests call pure functions only — no DB, no Telegram, no async.
"""
from __future__ import annotations

import pytest

from bot.services.morning_summary import (
    DayData,
    UserTargets,
    build_morning_summary,
    detect_signals,
    detect_trend,
)

# ── Shared fixtures ────────────────────────────────────────────────────────────

TARGETS = UserTargets(calories=2300, protein_g=160, fat_g=70, carbs_g=240)


def _day(
    calories: float,
    protein: float = 130,
    fat: float = 60,
    carbs: float = 210,
    meals: int = 3,
) -> DayData:
    return DayData(
        calories=calories,
        protein_g=protein,
        fat_g=fat,
        carbs_g=carbs,
        meals_count=meals,
    )


# ── Scenario 1: Fallback (no data) ────────────────────────────────────────────

class TestFallback:
    def test_zero_calories_shows_fallback(self):
        text = build_morning_summary("Алексей", _day(0, meals=0), TARGETS)
        assert "не записаны" in text or "не записан" in text

    def test_below_300_kcal_shows_fallback(self):
        text = build_morning_summary("Алексей", _day(200, meals=1), TARGETS)
        assert "не записаны" in text or "не записан" in text

    def test_fallback_includes_call_to_action(self):
        text = build_morning_summary("Алексей", _day(0, meals=0), TARGETS)
        assert any(w in text.lower() for w in ["напиши", "сфотографируй", "начни"])

    def test_fallback_includes_greeting(self):
        text = build_morning_summary("Алексей", _day(0, meals=0), TARGETS)
        assert "Алексей" in text

    def test_fallback_no_name(self):
        text = build_morning_summary("", _day(0, meals=0), TARGETS)
        assert "Доброе утро" in text


# ── Scenario 2: Good day ───────────────────────────────────────────────────────

class TestGoodDay:
    # 92% of calories (within 85–115%), protein 96% (>= 90%)
    YESTERDAY = _day(calories=2116, protein=154, fat=65, carbs=230)

    def test_good_day_signal_detected(self):
        signals = detect_signals(self.YESTERDAY, TARGETS)
        assert "good_day" in signals

    def test_no_bad_signals_on_good_day(self):
        signals = detect_signals(self.YESTERDAY, TARGETS)
        assert "low_calories" not in signals
        assert "high_calories" not in signals
        assert "low_protein" not in signals

    def test_good_day_message_has_positive_opening(self):
        text = build_morning_summary("Маша", self.YESTERDAY, TARGETS)
        assert any(w in text.lower() for w in ["план", "курс", "молодец", "хорош"])

    def test_good_day_message_contains_calories(self):
        text = build_morning_summary("Маша", self.YESTERDAY, TARGETS)
        assert "2116" in text


# ── Scenario 3: Low calories ──────────────────────────────────────────────────

class TestLowCalories:
    # 60% of 2300 target
    YESTERDAY = _day(calories=1380, protein=100, fat=50, carbs=160)

    def test_low_calories_signal(self):
        signals = detect_signals(self.YESTERDAY, TARGETS)
        assert "low_calories" in signals

    def test_no_high_calories_signal(self):
        signals = detect_signals(self.YESTERDAY, TARGETS)
        assert "high_calories" not in signals

    def test_low_calories_message_has_supportive_opening(self):
        text = build_morning_summary("Иван", self.YESTERDAY, TARGETS)
        assert any(w in text.lower() for w in ["маловато", "меньше", "не добрал"])

    def test_low_calories_recommendation_present(self):
        text = build_morning_summary("Иван", self.YESTERDAY, TARGETS)
        # Should suggest not skipping meals
        assert any(w in text.lower() for w in ["пропуск", "приём", "раз в день"])


# ── Scenario 4: High calories ─────────────────────────────────────────────────

class TestHighCalories:
    # 125% of 2300 target
    YESTERDAY = _day(calories=2875, protein=170, fat=90, carbs=300)

    def test_high_calories_signal(self):
        signals = detect_signals(self.YESTERDAY, TARGETS)
        assert "high_calories" in signals

    def test_no_low_calories_signal(self):
        signals = detect_signals(self.YESTERDAY, TARGETS)
        assert "low_calories" not in signals

    def test_high_calories_message_has_corrective_opening(self):
        text = build_morning_summary("Катя", self.YESTERDAY, TARGETS)
        assert any(w in text.lower() for w in ["выше", "больше", "превышен", "вышел", "вышла"])

    def test_high_calories_recommendation_present(self):
        text = build_morning_summary("Катя", self.YESTERDAY, TARGETS)
        assert any(w in text.lower() for w in ["порц", "снизить", "замен"])


# ── Scenario 5: Low protein ───────────────────────────────────────────────────

class TestLowProtein:
    # Calories on target (95%), but protein only 70% of target
    YESTERDAY = _day(calories=2185, protein=112, fat=68, carbs=235)

    def test_low_protein_signal(self):
        signals = detect_signals(self.YESTERDAY, TARGETS)
        assert "low_protein" in signals

    def test_calories_ok_no_cal_signal(self):
        signals = detect_signals(self.YESTERDAY, TARGETS)
        assert "low_calories" not in signals
        assert "high_calories" not in signals

    def test_low_protein_recommendation_in_message(self):
        text = build_morning_summary("Света", self.YESTERDAY, TARGETS)
        assert any(w in text.lower() for w in ["белк", "яйц", "творог", "курич", "бобов"])


# ── Scenario 6: High fat ──────────────────────────────────────────────────────

class TestHighFat:
    # Calories on target (93%), fat 130% of target
    YESTERDAY = _day(calories=2139, protein=150, fat=91, carbs=220)

    def test_high_fat_signal(self):
        signals = detect_signals(self.YESTERDAY, TARGETS)
        assert "high_fat" in signals

    def test_high_fat_recommendation_in_message(self):
        text = build_morning_summary("Дима", self.YESTERDAY, TARGETS)
        assert any(w in text.lower() for w in ["жир", "жарен", "варён", "запечён"])


# ── Scenario 7: Improving trend ───────────────────────────────────────────────

class TestImprovingTrend:
    # Day before: low calories (50%). Yesterday: better but still below (80%).
    DAY_BEFORE = _day(calories=1150)
    YESTERDAY = _day(calories=1840)

    def test_improving_trend_detected(self):
        trend = detect_trend(self.YESTERDAY, self.DAY_BEFORE, TARGETS)
        assert trend == "improving"

    def test_improving_trend_in_message(self):
        text = build_morning_summary("Роман", self.YESTERDAY, TARGETS, self.DAY_BEFORE)
        assert any(w in text.lower() for w in ["лучше", "правильн", "движеш"])


# ── Scenario 8: Stable good (two good days in a row) ─────────────────────────

class TestStableGood:
    # Both days within 85–115% and protein >= 90%
    DAY_BEFORE = _day(calories=2070, protein=148, fat=66, carbs=225)
    YESTERDAY = _day(calories=2185, protein=152, fat=64, carbs=232)

    def test_stable_good_trend(self):
        trend = detect_trend(self.YESTERDAY, self.DAY_BEFORE, TARGETS)
        assert trend == "stable_good"

    def test_stable_good_message_mentions_rhythm(self):
        text = build_morning_summary("Юля", self.YESTERDAY, TARGETS, self.DAY_BEFORE)
        assert any(w in text.lower() for w in ["2 дня", "ритм", "норм"])


# ── detect_signals edge cases ─────────────────────────────────────────────────

class TestDetectSignalsEdgeCases:
    def test_zero_target_returns_empty(self):
        targets = UserTargets(calories=0, protein_g=0, fat_g=0, carbs_g=0)
        signals = detect_signals(_day(1500), targets)
        assert signals == set()

    def test_exactly_at_boundary_70_pct(self):
        # Exactly 70% should NOT trigger low_calories (boundary is strictly <)
        day = _day(calories=2300 * 0.70)
        signals = detect_signals(day, TARGETS)
        assert "low_calories" not in signals

    def test_just_below_boundary_70_pct(self):
        day = _day(calories=2300 * 0.699)
        signals = detect_signals(day, TARGETS)
        assert "low_calories" in signals

    def test_good_day_requires_sufficient_protein(self):
        # Calories in range but protein only 80% — not a good day
        day = _day(calories=2100, protein=128)
        signals = detect_signals(day, TARGETS)
        assert "good_day" not in signals
