"""
Morning summary builder — pure rule-based logic, no DB access.

Takes yesterday's and (optionally) day-before's nutrition data,
compares against user targets, and returns a short friendly message.

Designed to be fully testable without any DB or Telegram setup.
"""
from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class DayData:
    calories: float
    protein_g: float
    fat_g: float
    carbs_g: float
    meals_count: int


@dataclass
class UserTargets:
    calories: float
    protein_g: float
    fat_g: float
    carbs_g: float


# ── Phrase pools ───────────────────────────────────────────────────────────────

_OPENING_GOOD = [
    "Вчера всё шло по плану — так держать.",
    "Хороший результат вчера.",
    "Вчера держишь курс — молодец.",
]

_OPENING_LOW_CAL = [
    "Вчера поел(а) немного меньше, чем нужно.",
    "Вчера калорий было маловато.",
    "Вчера не добрал(а) до нормы — ничего страшного.",
]

_OPENING_HIGH_CAL = [
    "Вчера немного вышел(а) за норму.",
    "Вчера было чуть больше, чем нужно.",
    "Вчера небольшое превышение — бывает.",
]

_OPENING_NEUTRAL = [
    "Держи сводку за вчера.",
    "Вот что было вчера.",
    "Итоги вчерашнего дня:",
]

_TREND_LINES = {
    "stable_good": "Уже 2 дня подряд в норме — отличный ритм.",
    "stable_bad": "2 дня подряд немного не попадаешь в цель — не сдавайся, ты справишься.",
    "improving": "Вчера лучше, чем позавчера — движешься в правильную сторону.",
    "worsening": "Вчера немного хуже, чем позавчера — сегодня новый шанс.",
}

_REC_LOW_PROTEIN = "Белка было маловато — добавь яйца, творог, курицу или бобовые."
_REC_HIGH_FAT = "Жиров многовато — меньше жареного, больше варёного или запечённого."
_REC_LOW_CAL = "Старайся не пропускать приёмы — 3–4 раза в день помогут набрать норму."
_REC_HIGH_CAL = "Попробуй немного снизить порции или заменить калорийный перекус овощами."
_REC_CARBS_LOW = "Углеводов было маловато — добавь кашу, хлеб или фрукты."
_REC_CARBS_HIGH = "Углеводов многовато — замени сладкое и белый хлеб на сложные углеводы."

_FALLBACK_NO_DATA = (
    "Вчера приёмы пищи не записаны.\n\n"
    "Начни день сейчас: напиши или сфотографируй, что ел(а) — я всё посчитаю."
)

# Signal priority for recommendation selection (first match wins)
_REC_PRIORITY = [
    ("low_protein", _REC_LOW_PROTEIN),
    ("high_fat", _REC_HIGH_FAT),
    ("low_calories", _REC_LOW_CAL),
    ("high_calories", _REC_HIGH_CAL),
    ("carbs_low", _REC_CARBS_LOW),
    ("carbs_high", _REC_CARBS_HIGH),
]


# ── Signal detection ───────────────────────────────────────────────────────────

def detect_signals(day: DayData, targets: UserTargets) -> set[str]:
    """
    Return a set of signal strings based on how the day compares to targets.

    Signals:
      low_calories   — consumed < 70% of calorie target
      high_calories  — consumed > 115% of calorie target
      low_protein    — protein < 75% of target
      high_fat       — fat > 120% of target
      carbs_low      — carbs < 65% of target
      carbs_high     — carbs > 125% of target
      good_day       — calories 85–115% AND protein >= 90%
    """
    signals: set[str] = set()
    if targets.calories <= 0:
        return signals

    cal_pct = day.calories / targets.calories
    prot_pct = day.protein_g / targets.protein_g if targets.protein_g > 0 else 1.0
    fat_pct = day.fat_g / targets.fat_g if targets.fat_g > 0 else 1.0
    carbs_pct = day.carbs_g / targets.carbs_g if targets.carbs_g > 0 else 1.0

    if cal_pct < 0.70:
        signals.add("low_calories")
    elif cal_pct > 1.15:
        signals.add("high_calories")

    if prot_pct < 0.75:
        signals.add("low_protein")
    if fat_pct > 1.20:
        signals.add("high_fat")
    if carbs_pct < 0.65:
        signals.add("carbs_low")
    elif carbs_pct > 1.25:
        signals.add("carbs_high")

    if 0.85 <= cal_pct <= 1.15 and prot_pct >= 0.90:
        signals.add("good_day")

    return signals


def detect_trend(
    yesterday: DayData,
    day_before: DayData,
    targets: UserTargets,
) -> str | None:
    """
    Compare two consecutive days and return a trend string or None.

    Trend signals:
      stable_good — both days good
      stable_bad  — both days low or high calories
      improving   — yesterday meaningfully closer to target than day before
      worsening   — yesterday meaningfully farther from target than day before
    """
    if targets.calories <= 0:
        return None

    yest_signals = detect_signals(yesterday, targets)
    prev_signals = detect_signals(day_before, targets)

    if "good_day" in yest_signals and "good_day" in prev_signals:
        return "stable_good"

    yest_bad = bool({"low_calories", "high_calories"} & yest_signals)
    prev_bad = bool({"low_calories", "high_calories"} & prev_signals)

    if yest_bad and prev_bad:
        return "stable_bad"

    # Distance to target as fraction of target
    yest_dist = abs(yesterday.calories - targets.calories) / targets.calories
    prev_dist = abs(day_before.calories - targets.calories) / targets.calories

    if yest_dist < prev_dist - 0.05:
        return "improving"
    if yest_dist > prev_dist + 0.05:
        return "worsening"

    return None


# ── Message builder ────────────────────────────────────────────────────────────

def build_morning_summary(
    name: str,
    yesterday: DayData,
    targets: UserTargets,
    day_before: DayData | None = None,
) -> str:
    """
    Build the morning summary message as an HTML-formatted string.

    Args:
        name:       User's preferred name (may be empty string).
        yesterday:  Nutrition data for the day being summarised.
        targets:    User's daily nutrition targets.
        day_before: Optional data from two days ago for trend analysis.

    Returns:
        HTML string ready to send via bot.send_message().
    """
    greeting = f"Доброе утро, {name}!\n\n" if name else "Доброе утро!\n\n"

    # Fallback: nothing meaningful logged
    if yesterday.calories < 300 or yesterday.meals_count == 0:
        return greeting + _FALLBACK_NO_DATA

    signals = detect_signals(yesterday, targets)

    # Opening phrase
    if "good_day" in signals:
        opening = random.choice(_OPENING_GOOD)
    elif "low_calories" in signals:
        opening = random.choice(_OPENING_LOW_CAL)
    elif "high_calories" in signals:
        opening = random.choice(_OPENING_HIGH_CAL)
    else:
        opening = random.choice(_OPENING_NEUTRAL)

    # Stats block
    def _pct(val: float, target: float) -> int:
        return int(val / target * 100) if target > 0 else 0

    kcal_pct = _pct(yesterday.calories, targets.calories)
    prot_pct = _pct(yesterday.protein_g, targets.protein_g)
    fat_pct = _pct(yesterday.fat_g, targets.fat_g)
    carbs_pct = _pct(yesterday.carbs_g, targets.carbs_g)

    stats_block = (
        f"Вчера\n"
        f"<b>{int(yesterday.calories)}</b> ккал из {int(targets.calories)} рекомендованных ({kcal_pct}%)\n"
        f"{int(yesterday.protein_g)}г белки, норма {int(targets.protein_g)}г ({prot_pct}%)\n"
        f"{int(yesterday.fat_g)}г жиры, норма {int(targets.fat_g)}г ({fat_pct}%)\n"
        f"{int(yesterday.carbs_g)}г углеводы, норма {int(targets.carbs_g)}г ({carbs_pct}%)"
    )

    # Trend (only if day_before has real data)
    trend_line = ""
    if day_before is not None and day_before.calories >= 300:
        trend = detect_trend(yesterday, day_before, targets)
        if trend:
            trend_line = _TREND_LINES.get(trend, "")

    # Recommendation (highest-priority signal)
    rec = ""
    for signal, text in _REC_PRIORITY:
        if signal in signals:
            rec = f"Моя рекомендация: {text}"
            break

    # Assemble sections separated by blank lines
    sections = [opening, stats_block]
    if trend_line:
        sections.append(trend_line)
    if rec:
        sections.append(rec)
    sections.append("Удачного дня!")

    return greeting + "\n\n".join(sections)
