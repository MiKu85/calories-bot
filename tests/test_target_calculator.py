"""
Unit tests for bot/services/target_calculator.py

All tests use pure functions — no DB, no async.
Expected values are hand-calculated and documented inline.
"""
from __future__ import annotations

import pytest

from bot.db.models import ActivityLevel, Goal, Sex
from bot.services.target_calculator import (
    FAT_PER_KG,
    ACTIVITY_MULTIPLIERS,
    GOAL_MULTIPLIERS,
    PROTEIN_PER_KG,
    UserTargets,
    calculate_bmr,
    calculate_targets,
)


# ── BMR tests ──────────────────────────────────────────────────────────────────

class TestCalculateBMR:
    def test_male(self):
        # 10*75 + 6.25*175 - 5*25 + 5 = 750 + 1093.75 - 125 + 5 = 1723.75
        result = calculate_bmr(Sex.male, weight_kg=75, height_cm=175, age=25)
        assert result == pytest.approx(1723.75)

    def test_female(self):
        # 10*60 + 6.25*165 - 5*30 - 161 = 600 + 1031.25 - 150 - 161 = 1320.25
        result = calculate_bmr(Sex.female, weight_kg=60, height_cm=165, age=30)
        assert result == pytest.approx(1320.25)

    def test_male_higher_than_female_same_params(self):
        # Male formula adds 5, female subtracts 161 → diff = 166
        male = calculate_bmr(Sex.male, 70, 170, 25)
        female = calculate_bmr(Sex.female, 70, 170, 25)
        assert male - female == pytest.approx(166.0)

    def test_heavier_person_has_higher_bmr(self):
        light = calculate_bmr(Sex.male, weight_kg=60, height_cm=170, age=25)
        heavy = calculate_bmr(Sex.male, weight_kg=90, height_cm=170, age=25)
        assert heavy > light

    def test_taller_person_has_higher_bmr(self):
        short = calculate_bmr(Sex.male, weight_kg=70, height_cm=160, age=25)
        tall = calculate_bmr(Sex.male, weight_kg=70, height_cm=190, age=25)
        assert tall > short

    def test_older_person_has_lower_bmr(self):
        young = calculate_bmr(Sex.male, weight_kg=70, height_cm=175, age=20)
        old = calculate_bmr(Sex.male, weight_kg=70, height_cm=175, age=60)
        assert old < young


# ── TDEE / calorie target tests ────────────────────────────────────────────────

class TestCalculateTargets:
    # Reference values (male, 75kg, 175cm, 25y, moderate activity, lose goal):
    # BMR = 1723.75
    # TDEE = 1723.75 * 1.55 = 2671.8125
    # Calories = 2671.8125 * 0.85 = 2271.040625 → rounds to 2271

    def _male_moderate_lose(self) -> UserTargets:
        return calculate_targets(
            sex=Sex.male,
            weight_kg=75,
            height_cm=175,
            age=25,
            activity_level=ActivityLevel.moderate,
            goal=Goal.lose,
        )

    def test_calories_rounded(self):
        result = self._male_moderate_lose()
        # 2671.8125 * 0.85 = 2271.040625 → round() = 2271
        assert result.daily_calories == 2271

    def test_protein_lose(self):
        result = self._male_moderate_lose()
        # 1.8 g/kg * 75 = 135.0
        assert result.daily_protein_g == pytest.approx(135.0)

    def test_fat(self):
        result = self._male_moderate_lose()
        # 0.8 g/kg * 75 = 60.0
        assert result.daily_fat_g == pytest.approx(60.0)

    def test_carbs_positive(self):
        result = self._male_moderate_lose()
        # (2271.040625 - 135*4 - 60*9) / 4 = (2271.040625 - 540 - 540) / 4 = 297.76...
        assert result.daily_carbs_g == pytest.approx(297.8, abs=0.5)
        assert result.daily_carbs_g > 0

    def test_maintain_higher_calories_than_lose(self):
        lose = calculate_targets(Sex.male, 75, 175, 25, ActivityLevel.moderate, Goal.lose)
        maintain = calculate_targets(Sex.male, 75, 175, 25, ActivityLevel.moderate, Goal.maintain)
        assert maintain.daily_calories > lose.daily_calories

    def test_gain_higher_calories_than_maintain(self):
        maintain = calculate_targets(Sex.male, 75, 175, 25, ActivityLevel.moderate, Goal.maintain)
        gain = calculate_targets(Sex.male, 75, 175, 25, ActivityLevel.moderate, Goal.gain)
        assert gain.daily_calories > maintain.daily_calories

    def test_protein_gain_higher_than_lose(self):
        lose = calculate_targets(Sex.male, 80, 180, 30, ActivityLevel.moderate, Goal.lose)
        gain = calculate_targets(Sex.male, 80, 180, 30, ActivityLevel.moderate, Goal.gain)
        # gain: 2.0 g/kg vs lose: 1.8 g/kg
        assert gain.daily_protein_g > lose.daily_protein_g

    def test_fat_same_regardless_of_goal(self):
        lose = calculate_targets(Sex.female, 60, 165, 28, ActivityLevel.light, Goal.lose)
        gain = calculate_targets(Sex.female, 60, 165, 28, ActivityLevel.light, Goal.gain)
        assert lose.daily_fat_g == gain.daily_fat_g

    def test_very_active_higher_than_sedentary(self):
        sedentary = calculate_targets(Sex.male, 80, 175, 30, ActivityLevel.sedentary, Goal.maintain)
        very_active = calculate_targets(Sex.male, 80, 175, 30, ActivityLevel.very_active, Goal.maintain)
        assert very_active.daily_calories > sedentary.daily_calories

    def test_carbs_never_negative(self):
        # Very low calorie scenario with high protein/fat → carbs should be clamped to 0
        # Use a tiny/very old person with gain goal (high protein) to stress-test
        result = calculate_targets(
            sex=Sex.female,
            weight_kg=30,   # minimum weight
            height_cm=100,  # minimum height
            age=100,        # maximum age
            activity_level=ActivityLevel.sedentary,
            goal=Goal.lose,
        )
        assert result.daily_carbs_g >= 0

    def test_female_moderate_maintain(self):
        result = calculate_targets(
            sex=Sex.female,
            weight_kg=60,
            height_cm=165,
            age=30,
            activity_level=ActivityLevel.moderate,
            goal=Goal.maintain,
        )
        # BMR = 1320.25, TDEE = 1320.25 * 1.55 = 2046.3875, maintain * 1.0 = 2046
        assert result.daily_calories == 2046
        # Protein maintain: 1.6 * 60 = 96.0
        assert result.daily_protein_g == pytest.approx(96.0)
        # Fat: 0.8 * 60 = 48.0
        assert result.daily_fat_g == pytest.approx(48.0)


# ── Constants sanity checks ────────────────────────────────────────────────────

class TestConstants:
    def test_all_activity_levels_covered(self):
        for level in ActivityLevel:
            assert level in ACTIVITY_MULTIPLIERS

    def test_all_goals_covered_in_multipliers(self):
        for goal in Goal:
            assert goal in GOAL_MULTIPLIERS

    def test_all_goals_covered_in_protein(self):
        for goal in Goal:
            assert goal in PROTEIN_PER_KG

    def test_activity_multipliers_ascending(self):
        values = [
            ACTIVITY_MULTIPLIERS[ActivityLevel.sedentary],
            ACTIVITY_MULTIPLIERS[ActivityLevel.light],
            ACTIVITY_MULTIPLIERS[ActivityLevel.moderate],
            ACTIVITY_MULTIPLIERS[ActivityLevel.active],
            ACTIVITY_MULTIPLIERS[ActivityLevel.very_active],
        ]
        assert values == sorted(values)

    def test_goal_multipliers_ascending(self):
        assert GOAL_MULTIPLIERS[Goal.lose] < GOAL_MULTIPLIERS[Goal.maintain] < GOAL_MULTIPLIERS[Goal.gain]

    def test_protein_per_kg_ascending(self):
        assert PROTEIN_PER_KG[Goal.lose] < PROTEIN_PER_KG[Goal.maintain] < PROTEIN_PER_KG[Goal.gain]

    def test_fat_per_kg_positive(self):
        assert FAT_PER_KG > 0
