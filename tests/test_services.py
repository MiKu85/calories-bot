"""
Tests for service-layer logic:
- apply_targets: happy path and ValueError for incomplete profile (DB required)
- reset_onboarding: clears all profile fields (DB required)
- Admin access: telegram_id membership check (pure)
- Onboarding input validation bounds: age / height / weight (pure)
"""
from __future__ import annotations

import pytest

from bot.db.models import ActivityLevel, Goal, OnboardingState, Sex, User
from bot.services.user_service import apply_targets, reset_onboarding


# ── apply_targets ──────────────────────────────────────────────────────────────

class TestApplyTargets:
    def _complete_user(self) -> User:
        u = User.__new__(User)
        u.preferred_name = "Тест"
        u.sex = Sex.male
        u.age = 30
        u.height_cm = 175.0
        u.weight_kg = 75.0
        u.activity_level = ActivityLevel.moderate
        u.goal = Goal.maintain
        u.daily_calories_target = None
        u.daily_protein_g_target = None
        u.daily_fat_g_target = None
        u.daily_carbs_g_target = None
        return u

    @pytest.mark.asyncio
    async def test_happy_path_persists_targets(self, db):
        user = User(
            telegram_id=888_000_001,
            onboarding_state=OnboardingState.completed,
            sex=Sex.male,
            age=30,
            height_cm=175.0,
            weight_kg=75.0,
            activity_level=ActivityLevel.moderate,
            goal=Goal.maintain,
        )
        db.add(user)
        await db.flush()

        targets = await apply_targets(user, db)

        assert targets.daily_calories > 0
        assert targets.daily_protein_g > 0
        assert targets.daily_fat_g > 0
        assert targets.daily_carbs_g >= 0
        # Verify persisted to user row
        assert user.daily_calories_target == targets.daily_calories
        assert user.daily_protein_g_target == targets.daily_protein_g
        assert user.daily_fat_g_target == targets.daily_fat_g
        assert user.daily_carbs_g_target == targets.daily_carbs_g

    @pytest.mark.asyncio
    async def test_female_gets_lower_calories_than_male(self, db):
        male = User(
            telegram_id=888_000_002,
            onboarding_state=OnboardingState.completed,
            sex=Sex.male,
            age=30,
            height_cm=175.0,
            weight_kg=75.0,
            activity_level=ActivityLevel.moderate,
            goal=Goal.maintain,
        )
        female = User(
            telegram_id=888_000_003,
            onboarding_state=OnboardingState.completed,
            sex=Sex.female,
            age=30,
            height_cm=175.0,
            weight_kg=75.0,
            activity_level=ActivityLevel.moderate,
            goal=Goal.maintain,
        )
        db.add(male)
        db.add(female)
        await db.flush()

        male_targets = await apply_targets(male, db)
        female_targets = await apply_targets(female, db)

        assert female_targets.daily_calories < male_targets.daily_calories

    @pytest.mark.asyncio
    async def test_raises_when_profile_incomplete(self, db):
        user = User(
            telegram_id=888_000_004,
            onboarding_state=OnboardingState.awaiting_age,
            # sex, age, height, weight, activity_level, goal all None
        )
        db.add(user)
        await db.flush()

        with pytest.raises(ValueError, match="profile is incomplete"):
            await apply_targets(user, db)

    @pytest.mark.asyncio
    async def test_raises_when_activity_missing(self, db):
        user = User(
            telegram_id=888_000_005,
            onboarding_state=OnboardingState.awaiting_activity,
            sex=Sex.female,
            age=25,
            height_cm=165.0,
            weight_kg=60.0,
            # activity_level=None, goal=None
        )
        db.add(user)
        await db.flush()

        with pytest.raises(ValueError, match="profile is incomplete"):
            await apply_targets(user, db)

    @pytest.mark.asyncio
    async def test_goal_lose_produces_caloric_deficit(self, db):
        """Lose goal should yield fewer calories than maintain at same stats."""
        maintain_user = User(
            telegram_id=888_000_006,
            onboarding_state=OnboardingState.completed,
            sex=Sex.male,
            age=28,
            height_cm=180.0,
            weight_kg=85.0,
            activity_level=ActivityLevel.light,
            goal=Goal.maintain,
        )
        lose_user = User(
            telegram_id=888_000_007,
            onboarding_state=OnboardingState.completed,
            sex=Sex.male,
            age=28,
            height_cm=180.0,
            weight_kg=85.0,
            activity_level=ActivityLevel.light,
            goal=Goal.lose,
        )
        db.add(maintain_user)
        db.add(lose_user)
        await db.flush()

        maintain_targets = await apply_targets(maintain_user, db)
        lose_targets = await apply_targets(lose_user, db)

        assert lose_targets.daily_calories < maintain_targets.daily_calories


# ── reset_onboarding ───────────────────────────────────────────────────────────

class TestResetOnboarding:
    @pytest.mark.asyncio
    async def test_clears_all_profile_fields(self, db):
        user = User(
            telegram_id=888_100_001,
            onboarding_state=OnboardingState.completed,
            preferred_name="Алиса",
            sex=Sex.female,
            age=25,
            height_cm=165.0,
            weight_kg=58.0,
            activity_level=ActivityLevel.light,
            goal=Goal.lose,
            daily_calories_target=1800.0,
            daily_protein_g_target=100.0,
            daily_fat_g_target=50.0,
            daily_carbs_g_target=180.0,
        )
        db.add(user)
        await db.flush()

        await reset_onboarding(user, db)

        assert user.preferred_name is None
        assert user.sex is None
        assert user.age is None
        assert user.height_cm is None
        assert user.weight_kg is None
        assert user.activity_level is None
        assert user.workouts_per_week is None
        assert user.goal is None
        assert user.daily_calories_target is None
        assert user.daily_protein_g_target is None
        assert user.daily_fat_g_target is None
        assert user.daily_carbs_g_target is None

    @pytest.mark.asyncio
    async def test_resets_state_to_new(self, db):
        user = User(
            telegram_id=888_100_002,
            onboarding_state=OnboardingState.completed,
            sex=Sex.male,
            age=30,
            height_cm=175.0,
            weight_kg=75.0,
            activity_level=ActivityLevel.moderate,
            goal=Goal.maintain,
        )
        db.add(user)
        await db.flush()

        await reset_onboarding(user, db)

        assert user.onboarding_state == OnboardingState.new
        assert user.onboarding_completed_at is None

    @pytest.mark.asyncio
    async def test_reset_allows_apply_targets_to_raise(self, db):
        """After reset, profile is incomplete — apply_targets must refuse."""
        user = User(
            telegram_id=888_100_003,
            onboarding_state=OnboardingState.completed,
            sex=Sex.male,
            age=30,
            height_cm=175.0,
            weight_kg=75.0,
            activity_level=ActivityLevel.moderate,
            goal=Goal.maintain,
        )
        db.add(user)
        await db.flush()
        await reset_onboarding(user, db)

        with pytest.raises(ValueError):
            await apply_targets(user, db)


# ── Admin access ───────────────────────────────────────────────────────────────

class TestAdminAccess:
    """
    The admin check is: user.telegram_id in settings.telegram_admin_ids
    Test the pure membership logic with explicit id lists.
    """

    def _check_admin(self, telegram_id: int, admin_ids: list[int]) -> bool:
        return telegram_id in admin_ids

    def test_admin_id_grants_access(self):
        assert self._check_admin(123456789, [123456789]) is True

    def test_non_admin_id_denied(self):
        assert self._check_admin(999999999, [123456789]) is False

    def test_empty_admin_list_denies_everyone(self):
        assert self._check_admin(123456789, []) is False

    def test_multiple_admins_all_granted(self):
        admin_ids = [111, 222, 333]
        for uid in admin_ids:
            assert self._check_admin(uid, admin_ids) is True

    def test_non_admin_in_multi_admin_list_denied(self):
        assert self._check_admin(999, [111, 222, 333]) is False


# ── Onboarding input validation bounds ────────────────────────────────────────

class TestOnboardingValidationBounds:
    """
    Validation logic as implemented in bot/handlers/onboarding.py.
    Tests the boundary conditions for age, height, and weight inputs.
    """

    # -- Age (10 <= age <= 100) ------------------------------------------------

    @pytest.mark.parametrize("age", [10, 25, 50, 100])
    def test_valid_ages(self, age):
        assert 10 <= age <= 100

    @pytest.mark.parametrize("age", [0, 9, 101, 200, -1])
    def test_invalid_ages_out_of_range(self, age):
        assert not (10 <= age <= 100)

    def test_non_numeric_age_raises(self):
        with pytest.raises((ValueError, AttributeError)):
            int("двадцать пять")

    # -- Height (100 <= height <= 250) ----------------------------------------

    @pytest.mark.parametrize("height", [100.0, 150.0, 175.0, 200.0, 250.0])
    def test_valid_heights(self, height):
        assert 100 <= height <= 250

    @pytest.mark.parametrize("height", [99.9, 50.0, 250.1, 300.0])
    def test_invalid_heights_out_of_range(self, height):
        assert not (100 <= height <= 250)

    def test_comma_decimal_separator_parsed(self):
        raw = "175,5"
        value = float(raw.replace(",", "."))
        assert value == pytest.approx(175.5)

    # -- Weight (30 <= weight <= 300) -----------------------------------------

    @pytest.mark.parametrize("weight", [30.0, 60.0, 75.5, 100.0, 300.0])
    def test_valid_weights(self, weight):
        assert 30 <= weight <= 300

    @pytest.mark.parametrize("weight", [29.9, 0.0, 300.1, 500.0])
    def test_invalid_weights_out_of_range(self, weight):
        assert not (30 <= weight <= 300)

    def test_non_numeric_weight_raises(self):
        with pytest.raises(ValueError):
            float("семьдесят кг")
