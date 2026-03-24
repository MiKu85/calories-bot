"""
Mifflin-St Jeor calorie and macro target calculator.

Pure functions — no DB, no side effects.
"""
from __future__ import annotations

from dataclasses import dataclass

from bot.db.models import ActivityLevel, Goal, Sex

ACTIVITY_MULTIPLIERS: dict[ActivityLevel, float] = {
    ActivityLevel.sedentary: 1.2,
    ActivityLevel.light: 1.375,
    ActivityLevel.moderate: 1.55,
    ActivityLevel.active: 1.725,
    ActivityLevel.very_active: 1.9,
}

GOAL_MULTIPLIERS: dict[Goal, float] = {
    Goal.lose: 0.85,
    Goal.maintain: 1.0,
    Goal.gain: 1.10,
}

PROTEIN_PER_KG: dict[Goal, float] = {
    Goal.lose: 1.8,
    Goal.maintain: 1.6,
    Goal.gain: 2.0,
}

FAT_PER_KG = 0.8


@dataclass(frozen=True)
class UserTargets:
    daily_calories: float
    daily_protein_g: float
    daily_fat_g: float
    daily_carbs_g: float


def calculate_bmr(sex: Sex, weight_kg: float, height_cm: float, age: int) -> float:
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return base + 5 if sex == Sex.male else base - 161


def calculate_targets(
    sex: Sex,
    weight_kg: float,
    height_cm: float,
    age: int,
    activity_level: ActivityLevel,
    goal: Goal,
) -> UserTargets:
    bmr = calculate_bmr(sex, weight_kg, height_cm, age)
    tdee = bmr * ACTIVITY_MULTIPLIERS[activity_level]
    calories = tdee * GOAL_MULTIPLIERS[goal]

    protein_g = PROTEIN_PER_KG[goal] * weight_kg
    fat_g = FAT_PER_KG * weight_kg

    protein_cal = protein_g * 4
    fat_cal = fat_g * 9
    carbs_g = max(0.0, (calories - protein_cal - fat_cal) / 4)

    return UserTargets(
        daily_calories=round(calories),
        daily_protein_g=round(protein_g, 1),
        daily_fat_g=round(fat_g, 1),
        daily_carbs_g=round(carbs_g, 1),
    )
