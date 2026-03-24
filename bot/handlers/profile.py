"""
/profile — просмотр профиля и редактирование ключевых полей.

Edit flows:
  - weight   → ask new value → validate → save → recalculate → show profile
  - activity → inline keyboard → save → recalculate → show profile
  - goal     → inline keyboard → save → recalculate → show profile
  - recalc   → recalculate from current data → show profile
  - reset    → redirect to /reset
"""
from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import ActivityLevel, Goal, OnboardingState, User
from bot.keyboards.onboarding import activity_kb, goal_kb
from bot.keyboards.profile import profile_kb
from bot.services.user_service import apply_targets

logger = structlog.get_logger(__name__)
router = Router(name="profile")

# ── Labels ─────────────────────────────────────────────────────────────────────

_SEX_LABEL = {"male": "мужской", "female": "женский"}

_ACTIVITY_LABEL = {
    "sedentary": "сидячий (без спорта)",
    "light": "лёгкая (1-2 раза/нед)",
    "moderate": "умеренная (3-4 раза/нед)",
    "active": "высокая (5+ раз/нед)",
    "very_active": "очень высокая (спортсмен)",
}

_GOAL_LABEL = {
    "lose": "похудеть",
    "maintain": "поддерживать вес",
    "gain": "набрать массу",
}

_MISSING = "не указано"


# ── Profile message builder ────────────────────────────────────────────────────

def build_profile_text(user: User) -> str:
    sex = _SEX_LABEL.get(user.sex.value if user.sex else "", _MISSING)
    activity = _ACTIVITY_LABEL.get(user.activity_level.value if user.activity_level else "", _MISSING)
    goal = _GOAL_LABEL.get(user.goal.value if user.goal else "", _MISSING)

    workouts = f"{user.workouts_per_week} раз/нед" if user.workouts_per_week is not None else _MISSING

    profile_lines = [
        "<b>Профиль</b>",
        f"Имя: {user.preferred_name or _MISSING}",
        f"Пол: {sex}",
        f"Возраст: {user.age or _MISSING}",
        f"Рост: {int(user.height_cm) if user.height_cm else _MISSING} см",
        f"Вес: {user.weight_kg or _MISSING} кг",
        f"Активность: {activity}",
        f"Тренировок в неделю: {workouts}",
        f"Цель: {goal}",
    ]

    if user.targets_set:
        profile_lines += [
            "",
            "<b>Цели на день</b>",
            f"Калории: {int(user.daily_calories_target)} ккал",
            f"Белки: {user.daily_protein_g_target} г",
            f"Жиры: {user.daily_fat_g_target} г",
            f"Углеводы: {user.daily_carbs_g_target} г",
        ]
    else:
        missing = _missing_fields(user)
        profile_lines += [
            "",
            f"Цели не рассчитаны — заполни профиль: {', '.join(missing)}.",
        ]

    return "\n".join(profile_lines)


def _missing_fields(user: User) -> list[str]:
    fields = []
    if not user.sex:
        fields.append("пол")
    if not user.age:
        fields.append("возраст")
    if not user.height_cm:
        fields.append("рост")
    if not user.weight_kg:
        fields.append("вес")
    if not user.activity_level:
        fields.append("активность")
    if not user.goal:
        fields.append("цель")
    return fields


# ── FSM ────────────────────────────────────────────────────────────────────────

class ProfileEditStates(StatesGroup):
    awaiting_weight = State()


# ── /profile command ───────────────────────────────────────────────────────────

@router.message(Command("profile"))
async def cmd_profile(message: Message, user: User, state: FSMContext) -> None:
    if user.onboarding_state != OnboardingState.completed:
        await message.answer("Сначала нужно заполнить профиль. Напиши /start.")
        return
    await state.clear()
    await message.answer(build_profile_text(user), reply_markup=profile_kb())


# ── Edit: weight ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "profile_edit:weight")
async def edit_weight_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(ProfileEditStates.awaiting_weight)
    await callback.message.answer("Введи новый вес (в кг, например: 74.5):")


@router.message(ProfileEditStates.awaiting_weight)
async def edit_weight_save(
    message: Message, state: FSMContext, user: User, db: AsyncSession
) -> None:
    try:
        weight = float(message.text.strip().replace(",", "."))
        if not 30 <= weight <= 300:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Введи вес числом от 30 до 300, например: 74.5")
        return

    user.weight_kg = weight
    await db.flush()
    await _recalc_and_show(message, user, db, state)


# ── Edit: activity ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "profile_edit:activity")
async def edit_activity_start(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("Выбери новый уровень активности:", reply_markup=activity_kb())


@router.callback_query(F.data.startswith("activity:"))
async def edit_activity_save(
    callback: CallbackQuery, state: FSMContext, user: User, db: AsyncSession
) -> None:
    # Only handle when NOT in onboarding
    from bot.handlers.onboarding import OnboardingStates
    current = await state.get_state()
    if current == OnboardingStates.awaiting_activity:
        return  # let onboarding handler take it

    await callback.answer()
    value = callback.data.split(":")[1]
    activity_map = {
        "sedentary": ActivityLevel.sedentary,
        "light": ActivityLevel.light,
        "moderate": ActivityLevel.moderate,
        "active": ActivityLevel.active,
        "very_active": ActivityLevel.very_active,
    }
    user.activity_level = activity_map[value]
    await db.flush()
    await _recalc_and_show(callback.message, user, db, state)


# ── Edit: goal ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "profile_edit:goal")
async def edit_goal_start(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("Выбери новую цель:", reply_markup=goal_kb())


@router.callback_query(F.data.startswith("goal:"))
async def edit_goal_save(
    callback: CallbackQuery, state: FSMContext, user: User, db: AsyncSession
) -> None:
    from bot.handlers.onboarding import OnboardingStates
    current = await state.get_state()
    if current == OnboardingStates.awaiting_goal:
        return  # let onboarding handler take it

    await callback.answer()
    value = callback.data.split(":")[1]
    goal_map = {"lose": Goal.lose, "maintain": Goal.maintain, "gain": Goal.gain}
    user.goal = goal_map[value]
    await db.flush()
    await _recalc_and_show(callback.message, user, db, state)


# ── Recalculate ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "profile_edit:recalc")
async def recalc_targets(
    callback: CallbackQuery, state: FSMContext, user: User, db: AsyncSession
) -> None:
    await callback.answer()
    await _recalc_and_show(callback.message, user, db, state)


# ── Reset (redirect) ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "profile_edit:reset")
async def profile_reset_redirect(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    from bot.handlers.reset import cmd_reset
    await cmd_reset(callback.message, state)


# ── Internal helper ────────────────────────────────────────────────────────────

async def _recalc_and_show(
    message: Message, user: User, db: AsyncSession, state: FSMContext
) -> None:
    await state.clear()
    missing = _missing_fields(user)
    if missing:
        await message.answer(
            f"Не могу рассчитать цели — заполни профиль: {', '.join(missing)}.\n"
            "Используй /start для продолжения настройки."
        )
        return

    targets = await apply_targets(user, db)
    logger.info("targets_recalculated", telegram_id=user.telegram_id)

    await message.answer(
        f"Цели пересчитаны!\n\n"
        f"Калории: <b>{int(targets.daily_calories)}</b> ккал\n"
        f"Белки: <b>{targets.daily_protein_g}</b> г\n"
        f"Жиры: <b>{targets.daily_fat_g}</b> г\n"
        f"Углеводы: <b>{targets.daily_carbs_g}</b> г"
    )
    await message.answer(build_profile_text(user), reply_markup=profile_kb())
