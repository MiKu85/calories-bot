"""
Meal input handler — text flow + shared pipeline for voice/photo reuse.

Catches plain text messages from users who have completed onboarding
and are not in any active FSM state (or are in correction state).

Pipeline (shared via run_meal_pipeline / save_and_reply_meal):
  text / transcription
    → TextProvider.analyze_meal(text)
    → needs_clarification? → ask to clarify, stop
    → save_and_reply_meal() → result message + keyboard
    → [✅ Верно] → confirm meal
    → [✏️ Исправить] → soft-delete + ask to re-describe
    → [📊 Статистика] → show /stats inline
"""
from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.ai import get_text_provider
from bot.ai.schemas import MealAnalysisResult
from bot.db.models import MealInputType, OnboardingState, User
from bot.keyboards.meal import meal_result_kb
from bot.services.meal_service import (
    confirm_meal,
    delete_meal,
    get_meal_by_id,
    get_today_aggregate,
    save_meal,
)
from bot.services.stats_service import format_meal_result, format_stats

logger = structlog.get_logger(__name__)
router = Router(name="meal")


class MealStates(StatesGroup):
    awaiting_correction = State()


# ── Filter: only users who finished onboarding ────────────────────────────────

class OnboardingCompleted:
    """Passes through only when user has completed onboarding and has targets set."""
    def __call__(self, message: Message, user: User) -> bool:
        return (
            user.onboarding_state == OnboardingState.completed
            and user.targets_set
        )


# ── Shared: save meal + build reply ───────────────────────────────────────────

async def save_and_reply_meal(
    message: Message,
    result: MealAnalysisResult,
    input_type: MealInputType,
    raw_input: str | None,
    user: User,
    db: AsyncSession,
    *,
    disclaimer: str | None = None,
) -> None:
    """
    Save a validated MealAnalysisResult and send the result message to the user.

    Called from text, voice, and photo handlers after analysis is complete.
    disclaimer: optional italicised note prepended to the response (used for photo).
    """
    meal = await save_meal(
        user=user,
        input_type=input_type,
        raw_input=raw_input,
        result=result,
        db=db,
    )

    agg = await get_today_aggregate(user.id, db)
    response = format_meal_result(
        meal_calories=result.total_calories,
        meal_protein=result.total_protein_g,
        meal_fat=result.total_fat_g,
        meal_carbs=result.total_carbs_g,
        meal_items=meal.meal_items,
        agg=agg,
        user=user,
    )

    if disclaimer:
        response = f"<i>{disclaimer}</i>\n\n{response}"

    await message.answer(response, reply_markup=meal_result_kb(meal.id))
    logger.bind(telegram_id=user.telegram_id).info(
        "meal_saved",
        meal_id=meal.id,
        input_type=input_type.value,
        confidence=result.confidence,
    )


# ── Shared: text/voice analysis pipeline ──────────────────────────────────────

async def run_meal_pipeline(
    message: Message,
    text: str,
    input_type: MealInputType,
    user: User,
    db: AsyncSession,
) -> None:
    """
    Text-based meal pipeline: analyze → clarify or save+reply.
    Reused by both text and voice handlers.
    """
    log = logger.bind(
        telegram_id=user.telegram_id,
        input_type=input_type.value,
        input_preview=text[:60],
    )

    provider = get_text_provider()
    try:
        result = await provider.analyze_meal(text)
    except Exception as exc:
        log.error("meal_analysis_failed", error=str(exc))
        await message.answer(
            "Не смог обработать запрос — попробуй ещё раз или опиши иначе."
        )
        return

    if result.needs_clarification:
        clarification = result.clarification_prompt or (
            "Не совсем понял. Опиши подробнее — что именно ел(а) и примерный объём?"
        )
        if result.confidence_notes:
            clarification = f"{clarification}\n\n<i>Причина: {result.confidence_notes}</i>"
        await message.answer(clarification)
        return

    await save_and_reply_meal(
        message=message,
        result=result,
        input_type=input_type,
        raw_input=text,
        user=user,
        db=db,
    )


# ── Text meal handler ─────────────────────────────────────────────────────────

@router.message(
    OnboardingCompleted(),
    StateFilter(None, MealStates.awaiting_correction),
    F.text,
)
async def handle_text_meal(
    message: Message,
    user: User,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    await state.clear()
    await run_meal_pipeline(
        message=message,
        text=message.text.strip(),
        input_type=MealInputType.text,
        user=user,
        db=db,
    )


# ── ✅ Confirm ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("meal_ok:"))
async def meal_confirm_callback(
    callback: CallbackQuery, user: User, db: AsyncSession
) -> None:
    await callback.answer("Записано!")
    meal_id = int(callback.data.split(":")[1])
    meal = await get_meal_by_id(meal_id, db)

    if meal and meal.user_id == user.id and not meal.is_deleted:
        await confirm_meal(meal, db)

    await callback.message.edit_reply_markup(reply_markup=None)


# ── ✏️ Correct ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("meal_fix:"))
async def meal_fix_callback(
    callback: CallbackQuery,
    user: User,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    await callback.answer()
    meal_id = int(callback.data.split(":")[1])
    meal = await get_meal_by_id(meal_id, db)

    if meal and meal.user_id == user.id and not meal.is_deleted:
        await delete_meal(meal, db)

    await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(MealStates.awaiting_correction)
    await callback.message.answer(
        "Опиши приём пищи заново — текстом или голосом, и я пересчитаю."
    )


# ── 📊 Inline stats ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "meal_stats")
async def meal_stats_callback(
    callback: CallbackQuery, user: User, db: AsyncSession
) -> None:
    await callback.answer()
    agg = await get_today_aggregate(user.id, db)
    await callback.message.answer(format_stats(agg, user))


# ── Onboarding not complete guard ─────────────────────────────────────────────

@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def handle_text_no_profile(message: Message, user: User) -> None:
    """Catch-all for users who haven't finished onboarding."""
    if user.onboarding_state != OnboardingState.completed:
        await message.answer("Сначала нужно заполнить профиль. Напиши /start.")
    elif not user.targets_set:
        await message.answer(
            "Профиль неполный — зайди в /profile и заполни недостающие поля."
        )
