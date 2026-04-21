"""
Stats service — daily progress formatting and status phrases.

Pure formatting functions have no DB dependency and are easily testable.

Day boundary note: all aggregates use UTC date. Timezone support is
a future improvement; for MVP this is the accepted simplification.
"""
from __future__ import annotations

import random

from bot.db.models import DailyAggregate, User

# ── Supportive phrases (post-meal) ────────────────────────────────────────────

_PHRASES = [
    "Записал(а). Так держать!",
    "Отлично, продолжай в том же духе!",
    "Всё фиксирую — молодец!",
    "Записано. Следи за остатком!",
    "Хороший выбор, записал(а)!",
    "Готово. Двигаемся дальше!",
    "Принято. Ты на верном пути!",
    "Записал(а). Ещё один шаг к цели!",
]


def get_supportive_phrase() -> str:
    return random.choice(_PHRASES)


# ── Status phrase (used in /stats) ────────────────────────────────────────────

def get_status_phrase(consumed_calories: float, target_calories: float) -> str:
    """
    Short Russian status line based on how much of the calorie target is used.

    Thresholds:
      < 85%   → ещё есть запас
      85-105% → идёт по плану
      105-115% → чуть выше плана
      > 115%   → превышение нормы
    """
    if target_calories <= 0:
        return ""
    pct = consumed_calories / target_calories

    if pct < 0.85:
        return "Ещё есть запас — не забудь поесть."
    elif pct <= 1.05:
        return "Идёт по плану — отличная работа."
    elif pct <= 1.15:
        return "Чуть выше плана — следи за остатком."
    else:
        return "Превышение нормы. Постарайся не добирать больше сегодня."


# ── Internal helpers ──────────────────────────────────────────────────────────

def _remaining_str(consumed: float, target: float, unit: str) -> str:
    """
    Format remaining amount. When over target, show 'перебор' instead of
    a negative number — honest but not shaming.
    """
    remaining = target - consumed
    if remaining >= 0:
        return f"{int(remaining)}{unit}"
    else:
        return f"<i>перебор {int(abs(remaining))}{unit}</i>"


def _macro_remaining(label: str, consumed: float, target: float, unit: str = "г") -> str:
    remaining = target - consumed
    sign = "" if remaining >= 0 else "−"
    return f"{label} {sign}{int(abs(remaining))}{unit}"


# ── Message formatters ────────────────────────────────────────────────────────

def _format_portion(portion_description: str, is_photo: bool) -> str:
    """
    Clean up portion description for display.

    For text input: strip leading ~ (weight is exact as stated by user).
    For photo input: replace leading ~ with ≈ (visual estimate).
    """
    s = portion_description.strip()
    if s.startswith("~"):
        s = ("≈" if is_photo else "") + s[1:].strip()
    return s


def format_meal_result(
    meal_calories: float,
    meal_protein: float,
    meal_fat: float,
    meal_carbs: float,
    meal_items: list[dict] | None,
    agg: DailyAggregate,
    user: User,
    is_photo: bool = False,
) -> str:
    """
    Message shown immediately after a meal is saved (before user confirmation).

    Format:
      🍽 Приём пищи:

      1. <b>Название</b> 100 г
         18 ккал · Б 1 · Ж 0 · У 4

      <b>Этот приём:</b> 253 ккал · Б 15 · Ж 18 · У 9

      <b>Сегодня:</b> 382 ккал из 1736
      <b>Осталось:</b> 1354 ккал · Б 88 · Ж 22 · У 198
    """
    lines: list[str] = []

    # Header + numbered item list
    if meal_items:
        lines.append("🍽 <b>Приём пищи:</b>")
        lines.append("")
        for idx, item in enumerate(meal_items, start=1):
            kcal = int(item["calories"])
            prot = int(item.get("protein_g", 0))
            fat = int(item.get("fat_g", 0))
            carbs = int(item.get("carbs_g", 0))
            portion = _format_portion(item.get("portion_description", ""), is_photo)
            lines.append(f"{idx}. <b>{item['name']}</b> {portion}")
            lines.append(f"   {kcal} ккал · Б {prot} · Ж {fat} · У {carbs}")
            lines.append("")

    # This meal totals
    lines.append(
        f"<b>Этот приём:</b> {int(meal_calories)} ккал"
        f" · Б {meal_protein:.0f} · Ж {meal_fat:.0f} · У {meal_carbs:.0f}"
    )
    lines.append("")

    # Daily progress
    if user.targets_set and user.daily_calories_target:
        lines.append(
            f"<b>Сегодня:</b> {int(agg.total_calories)} ккал"
            f" из {int(user.daily_calories_target)}"
        )
        cal_str = _remaining_str(agg.total_calories, user.daily_calories_target, "ккал")
        prot_str = _macro_remaining("Б", agg.total_protein_g, user.daily_protein_g_target)
        fat_str = _macro_remaining("Ж", agg.total_fat_g, user.daily_fat_g_target)
        carbs_str = _macro_remaining("У", agg.total_carbs_g, user.daily_carbs_g_target)
        lines.append(f"<b>Осталось:</b> {cal_str} · {prot_str} · {fat_str} · {carbs_str}")
    else:
        lines.append(
            f"<b>Сегодня:</b> {int(agg.total_calories)} ккал"
            f" · Б {agg.total_protein_g:.0f} · Ж {agg.total_fat_g:.0f}"
            f" · У {agg.total_carbs_g:.0f}"
        )

    return "\n".join(lines)


def format_stats(agg: DailyAggregate, user: User) -> str:
    """
    Full daily stats for /stats command.

    Edge cases:
      - agg.meals_count == 0  → show "нет приёмов" message
      - targets not set       → show raw consumed without targets
      - over target           → show 'перебор' with positive number (no shaming)
    """
    lines: list[str] = ["<b>Статистика за сегодня</b>", ""]

    # No meals yet today
    if agg.meals_count == 0:
        lines.append("Сегодня приёмов пищи пока нет.")
        if user.targets_set:
            lines.append("")
            lines.append(f"Цель на день: {int(user.daily_calories_target)} ккал")
        lines.append("")
        lines.append("Напиши, что ел(а), — текстом, голосом или пришли фото.")
        return "\n".join(lines)

    if user.targets_set:
        # Row: Label consumed / target  (remaining or overrun)
        def stat_row(label: str, consumed: float, target: float, unit: str = "ккал") -> str:
            pct = int(consumed / target * 100) if target > 0 else 0
            rem = _remaining_str(consumed, target, unit)
            return f"{label}: {int(consumed)} / {int(target)} {unit}  ({rem},  {pct}%)"

        lines.append(stat_row("Калории", agg.total_calories, user.daily_calories_target))
        lines.append(stat_row("Белки  ", agg.total_protein_g, user.daily_protein_g_target, "г"))
        lines.append(stat_row("Жиры   ", agg.total_fat_g, user.daily_fat_g_target, "г"))
        lines.append(stat_row("Углеводы", agg.total_carbs_g, user.daily_carbs_g_target, "г"))
    else:
        # No targets set — show what was consumed
        lines.append(f"Калории: {int(agg.total_calories)} ккал")
        lines.append(f"Белки: {agg.total_protein_g:.1f} г")
        lines.append(f"Жиры: {agg.total_fat_g:.1f} г")
        lines.append(f"Углеводы: {agg.total_carbs_g:.1f} г")
        lines.append("")
        lines.append(
            "Цели не рассчитаны. Зайди в /profile и заполни профиль полностью."
        )

    lines.append("")
    lines.append(f"Приёмов пищи: {agg.meals_count}")

    if user.targets_set:
        lines.append("")
        lines.append(get_status_phrase(agg.total_calories, user.daily_calories_target))

    return "\n".join(lines)
