"""
Meal rating service — rule-based short evaluation block.

The short block is shown inline after the user confirms a meal (✅ Верно).
It is generated instantly from numbers — no LLM call, no latency.

Format:
  ───
  📊 Этот приём — X% от дневной цели
  ✅ [one positive observation]
  💡 [one concrete recommendation]

Only shown when meal calories ≥ 50 kcal and user has targets set.
"""
from __future__ import annotations

from bot.db.models import DailyAggregate, User


_SEPARATOR = "─" * 16


def build_short_rating(
    meal_calories: float,
    meal_protein: float,
    meal_fat: float,
    meal_carbs: float,
    agg: DailyAggregate,
    user: User,
) -> str | None:
    """
    Return a short rating block string, or None if rating should not be shown.

    Args:
        meal_calories/protein/fat/carbs: values for this specific meal.
        agg: daily aggregate AFTER this meal was counted.
        user: the user (for targets).
    """
    if meal_calories < 50:
        return None
    if not user.targets_set:
        return None

    target_cal = user.daily_calories_target or 0
    target_prot = user.daily_protein_g_target or 0
    target_fat = user.daily_fat_g_target or 0
    target_carbs = user.daily_carbs_g_target or 0

    if target_cal <= 0:
        return None

    meal_pct = int(meal_calories / target_cal * 100)

    # Determine meal-level macro balance for feedback
    # (assess relative to expected per-meal share, assuming ~3 meals/day)
    expected_prot = target_prot / 3
    expected_fat = target_fat / 3
    expected_carbs = target_carbs / 3

    prot_ratio = meal_protein / expected_prot if expected_prot > 0 else 1.0
    fat_ratio = meal_fat / expected_fat if expected_fat > 0 else 1.0
    carbs_ratio = meal_carbs / expected_carbs if expected_carbs > 0 else 1.0

    # Also check daily remaining to give forward-looking advice
    remaining_cal = target_cal - agg.total_calories

    # Build plus (positive observation)
    plus = _pick_plus(prot_ratio, fat_ratio, carbs_ratio, meal_calories, target_cal)

    # Build recommendation
    rec = _pick_rec(prot_ratio, fat_ratio, carbs_ratio, remaining_cal, target_prot, meal_protein)

    lines = [
        _SEPARATOR,
        f"📊 Этот приём — {meal_pct}% от дневной цели",
    ]
    if plus:
        lines.append(f"✅ {plus}")
    if rec:
        lines.append(f"💡 {rec}")
    lines.append(_SEPARATOR)

    return "\n".join(lines)


def _pick_plus(
    prot_ratio: float,
    fat_ratio: float,
    carbs_ratio: float,
    meal_cal: float,
    target_cal: float,
) -> str:
    """Pick the best positive observation about this meal."""
    meal_pct = meal_cal / target_cal if target_cal > 0 else 0

    if prot_ratio >= 1.1:
        return "Хороший уровень белка — сытость обеспечена надолго"
    if 0.15 <= meal_pct <= 0.40:
        return "Умеренная калорийность — приём вписывается в план"
    if fat_ratio <= 0.8 and prot_ratio >= 0.8:
        return "Сбалансированный приём по белкам и жирам"
    if carbs_ratio >= 1.0 and prot_ratio >= 0.8:
        return "Хорошее сочетание углеводов и белка для энергии"
    if prot_ratio >= 0.9:
        return "Достаточно белка в этом приёме"
    return "Приём зафиксирован — продолжай в том же духе"


def _pick_rec(
    prot_ratio: float,
    fat_ratio: float,
    carbs_ratio: float,
    remaining_cal: float,
    target_prot: float,
    meal_protein: float,
) -> str:
    """Pick the most actionable recommendation."""
    if prot_ratio < 0.6:
        deficit_g = int(target_prot / 3 - meal_protein)
        return (
            f"Маловато белка — добавь {deficit_g}–{deficit_g + 20}г: "
            "100г куриной грудки, 2 яйца или стакан кефира"
        )
    if fat_ratio > 1.5:
        return "Жиров многовато — при следующем приёме выбери варёное или запечённое вместо жареного"
    if carbs_ratio < 0.5:
        return "Мало углеводов — добавь кашу, цельнозерновой хлеб или фрукт для энергии"
    if remaining_cal < 200:
        return "Дневная норма почти выполнена — следующий приём сделай лёгким"
    if remaining_cal > 800:
        return "Ещё много запаса — не забудь поесть ещё раз сегодня"
    return "Добавь пару горстей овощей к следующему приёму — клетчатка и витамины без лишних калорий"
