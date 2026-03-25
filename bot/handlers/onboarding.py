"""
Onboarding FSM.

States (aiogram) mirror OnboardingState enum in DB — both are updated together.
DB state is the source of truth: if the bot restarts mid-onboarding,
resume_onboarding() restores the correct aiogram state from DB.

State transitions:
  new → awaiting_name → awaiting_sex → awaiting_age → awaiting_height
      → awaiting_weight → awaiting_activity → awaiting_workouts → awaiting_goal
      → completed
"""
from __future__ import annotations

from datetime import datetime, timezone

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import ActivityLevel, Goal, OnboardingState, Sex, User
from bot.keyboards.onboarding import (
    activity_kb,
    goal_kb,
    remove_kb,
    sex_kb,
)
from bot.services.user_service import apply_targets

logger = structlog.get_logger(__name__)
router = Router(name="onboarding")


# ── FSM States ─────────────────────────────────────────────────────────────────

class OnboardingStates(StatesGroup):
    awaiting_name = State()
    awaiting_sex = State()
    awaiting_age = State()
    awaiting_height = State()
    awaiting_weight = State()
    awaiting_activity = State()
    awaiting_workouts = State()
    awaiting_goal = State()


# Map DB state → aiogram state (for resume after restart)
_DB_TO_FSM: dict[OnboardingState, State] = {
    OnboardingState.awaiting_name: OnboardingStates.awaiting_name,
    OnboardingState.awaiting_sex: OnboardingStates.awaiting_sex,
    OnboardingState.awaiting_age: OnboardingStates.awaiting_age,
    OnboardingState.awaiting_height: OnboardingStates.awaiting_height,
    OnboardingState.awaiting_weight: OnboardingStates.awaiting_weight,
    OnboardingState.awaiting_activity: OnboardingStates.awaiting_activity,
    OnboardingState.awaiting_workouts: OnboardingStates.awaiting_workouts,
    OnboardingState.awaiting_goal: OnboardingStates.awaiting_goal,
}


# ── Resume helper ──────────────────────────────────────────────────────────────

async def resume_onboarding(message: Message, user: User, state: FSMContext) -> None:
    """Restore aiogram FSM state from DB and re-ask the current question."""
    # awaiting_workouts was removed — treat it as awaiting_goal
    if user.onboarding_state == OnboardingState.awaiting_workouts:
        user.onboarding_state = OnboardingState.awaiting_goal
        # db.flush() will happen via middleware commit

    fsm_state = _DB_TO_FSM.get(user.onboarding_state)
    if fsm_state:
        await state.set_state(fsm_state)

    ask_fn = {
        OnboardingState.awaiting_name: _send_ask_name,
        OnboardingState.awaiting_sex: _send_ask_sex,
        OnboardingState.awaiting_age: _send_ask_age,
        OnboardingState.awaiting_height: _send_ask_height,
        OnboardingState.awaiting_weight: _send_ask_weight,
        OnboardingState.awaiting_activity: _send_ask_activity,
        OnboardingState.awaiting_goal: _send_ask_goal,
    }.get(user.onboarding_state)

    if ask_fn:
        await ask_fn(message)


# ── Step entry points (called from start.py) ──────────────────────────────────

async def ask_name(
    message: Message, user: User, state: FSMContext, db: AsyncSession
) -> None:
    user.onboarding_state = OnboardingState.awaiting_name
    await db.flush()
    await state.set_state(OnboardingStates.awaiting_name)
    await _send_ask_name(message)


# ── Internal senders ──────────────────────────────────────────────────────────

async def _send_ask_name(message: Message) -> None:
    await message.answer("Как тебя зовут? (можно имя или никнейм)")


async def _send_ask_sex(message: Message) -> None:
    await message.answer("Укажи пол:", reply_markup=sex_kb())


async def _send_ask_age(message: Message) -> None:
    await message.answer("Сколько тебе лет?")


async def _send_ask_height(message: Message) -> None:
    await message.answer("Какой у тебя рост? (в сантиметрах, например: 175)")


async def _send_ask_weight(message: Message) -> None:
    await message.answer("Какой у тебя вес? (в кг, например: 72.5)")


async def _send_ask_activity(message: Message) -> None:
    await message.answer(
        "Как часто ты занимаешься спортом (кардио, силовые)?",
        reply_markup=activity_kb(),
    )


async def _send_ask_goal(message: Message) -> None:
    await message.answer("Какая твоя цель?", reply_markup=goal_kb())


# ── Step 1: Name ───────────────────────────────────────────────────────────────

@router.message(OnboardingStates.awaiting_name)
async def process_name(message: Message, state: FSMContext, user: User, db: AsyncSession) -> None:
    if not message.text:
        await message.answer("Напиши своё имя текстом.")
        return

    name = message.text.strip()[:64]
    if not name:
        await message.answer("Имя не может быть пустым. Попробуй ещё раз.")
        return

    user.preferred_name = name
    user.onboarding_state = OnboardingState.awaiting_sex
    await db.flush()
    await state.set_state(OnboardingStates.awaiting_sex)
    await _send_ask_sex(message)


# ── Step 2: Sex ────────────────────────────────────────────────────────────────

@router.callback_query(OnboardingStates.awaiting_sex, F.data.startswith("sex:"))
async def process_sex(
    callback: CallbackQuery, state: FSMContext, user: User, db: AsyncSession
) -> None:
    await callback.answer()
    value = callback.data.split(":")[1]

    user.sex = Sex.male if value == "male" else Sex.female
    user.onboarding_state = OnboardingState.awaiting_age
    await db.flush()
    await state.set_state(OnboardingStates.awaiting_age)
    await _send_ask_age(callback.message)


@router.message(OnboardingStates.awaiting_sex)
async def process_sex_text(message: Message) -> None:
    await message.answer("Выбери пол кнопкой выше.", reply_markup=sex_kb())


# ── Step 3: Age ────────────────────────────────────────────────────────────────

@router.message(OnboardingStates.awaiting_age)
async def process_age(message: Message, state: FSMContext, user: User, db: AsyncSession) -> None:
    try:
        age = int(message.text.strip())
        if not 10 <= age <= 100:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Введи возраст числом от 10 до 100.")
        return

    user.age = age
    user.onboarding_state = OnboardingState.awaiting_height
    await db.flush()
    await state.set_state(OnboardingStates.awaiting_height)
    await _send_ask_height(message)


# ── Step 4: Height ─────────────────────────────────────────────────────────────

@router.message(OnboardingStates.awaiting_height)
async def process_height(message: Message, state: FSMContext, user: User, db: AsyncSession) -> None:
    try:
        height = float(message.text.strip().replace(",", "."))
        if not 100 <= height <= 250:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Введи рост в сантиметрах (от 100 до 250), например: 175")
        return

    user.height_cm = height
    user.onboarding_state = OnboardingState.awaiting_weight
    await db.flush()
    await state.set_state(OnboardingStates.awaiting_weight)
    await _send_ask_weight(message)


# ── Step 5: Weight ─────────────────────────────────────────────────────────────

@router.message(OnboardingStates.awaiting_weight)
async def process_weight(message: Message, state: FSMContext, user: User, db: AsyncSession) -> None:
    try:
        weight = float(message.text.strip().replace(",", "."))
        if not 30 <= weight <= 300:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Введи вес в кг (от 30 до 300), например: 72.5")
        return

    user.weight_kg = weight
    user.onboarding_state = OnboardingState.awaiting_activity
    await db.flush()
    await state.set_state(OnboardingStates.awaiting_activity)
    await _send_ask_activity(message)


# ── Step 6: Activity ───────────────────────────────────────────────────────────

@router.callback_query(OnboardingStates.awaiting_activity, F.data.startswith("activity:"))
async def process_activity(
    callback: CallbackQuery, state: FSMContext, user: User, db: AsyncSession
) -> None:
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
    user.onboarding_state = OnboardingState.awaiting_goal
    await db.flush()
    await state.set_state(OnboardingStates.awaiting_goal)
    await _send_ask_goal(callback.message)


@router.message(OnboardingStates.awaiting_activity)
async def process_activity_text(message: Message) -> None:
    await message.answer("Выбери уровень активности кнопкой выше.", reply_markup=activity_kb())


# ── Step 7: Goal → complete onboarding ────────────────────────────────────────

@router.callback_query(OnboardingStates.awaiting_goal, F.data.startswith("goal:"))
async def process_goal(
    callback: CallbackQuery, state: FSMContext, user: User, db: AsyncSession
) -> None:
    await callback.answer()
    value = callback.data.split(":")[1]

    goal_map = {"lose": Goal.lose, "maintain": Goal.maintain, "gain": Goal.gain}
    user.goal = goal_map[value]
    user.onboarding_state = OnboardingState.completed
    user.onboarding_completed_at = datetime.now(timezone.utc)
    await db.flush()

    targets = await apply_targets(user, db)
    await state.clear()

    goal_label = {
        "lose": "похудеть",
        "maintain": "поддерживать текущий вес",
        "gain": "набрать мышечную массу",
    }[value]

    kcal = int(targets.daily_calories)
    prot = targets.daily_protein_g
    fat = targets.daily_fat_g
    carbs = targets.daily_carbs_g

    prot_pct = round(prot * 4 / kcal * 100) if kcal else 0
    fat_pct = round(fat * 9 / kcal * 100) if kcal else 0
    carbs_pct = 100 - prot_pct - fat_pct

    await callback.message.answer(
        f"Обязательно помогу тебе правильно питаться и чувствовать себя лучше с каждым днём.\n\n"
        f"Чтобы <b>{goal_label}</b>, рекомендую тебе есть около <b>{kcal} ккал/день</b> — "
        f"твоя потребность в калориях согласно твоим показателям.\n\n"
        f"<b>Распределение макронутриентов при {kcal} ккал/день:</b>\n\n"
        f"• Углеводы: примерно <b>{int(carbs)} г</b> ({carbs_pct}% от общего количества калорий)\n"
        f"• Белки: примерно <b>{int(prot)} г</b> ({prot_pct}% от общего количества калорий)\n"
        f"• Жиры: примерно <b>{int(fat)} г</b> ({fat_pct}% от общего количества калорий)\n\n"
        f"Постараемся вместе сделать твоё питание более разнообразным и сбалансированным. "
        f"Помни, что небольшие изменения каждый день дают большой эффект на дистанции.\n\n"
        f"Теперь просто пиши, что ел(а), — текстом, голосом или фото. Я всё посчитаю."
    )

    logger.info("onboarding_completed", telegram_id=callback.from_user.id)


@router.message(OnboardingStates.awaiting_goal)
async def process_goal_text(message: Message) -> None:
    await message.answer("Выбери цель кнопкой выше.", reply_markup=goal_kb())
