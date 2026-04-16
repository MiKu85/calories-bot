"""
Retrospective meal input — record meals for yesterday or the day before.

Two entry points:
  1. Date marker in text:  «вчера на ужин была курица» → bot asks for confirmation.
  2. /retro command        → bot prompts which day, then accepts meal input.

Constraints:
  - Max lookback: 2 days (yesterday / day-before-yesterday).
  - After selecting the retro date, the user inputs meal as usual
    (text / voice / photo) and the meal is saved with logged_at set
    to midnight UTC of the chosen day.
  - Editing / "Исправить" for retro meals works the same as today's meals.

FSM states:
  RetroStates.awaiting_date_confirm  — bot showed "Записать за вчера?" and waits
  RetroStates.awaiting_retro_meal    — user chose a day, now enter meal normally

Detected marker words (case-insensitive, Russian):
  вчера, позавчера, вчерашний, вчера вечером, прошлым вечером, вчера утром
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import structlog
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import MealInputType, User
from bot.handlers.meal import OnboardingCompleted
from bot.services.meal_service import recalculate_daily_aggregate

logger = structlog.get_logger(__name__)
router = Router(name="retro")

# ── FSM states ─────────────────────────────────────────────────────────────────

class RetroStates(StatesGroup):
    awaiting_date_confirm = State()  # bot asked "Записать за вчера?"
    awaiting_retro_meal = State()    # user confirmed date, waiting for meal input


# ── Date marker detection ──────────────────────────────────────────────────────

_YESTERDAY_PATTERNS = re.compile(
    r"\b(вчера|вчерашн\w*|вчера\s+(?:вечером|утром|днём|в\s+обед)|прошлым\s+вечером)\b",
    re.IGNORECASE | re.UNICODE,
)
_DAY_BEFORE_PATTERNS = re.compile(
    r"\b(позавчера)\b",
    re.IGNORECASE | re.UNICODE,
)


def detect_retro_marker(text: str) -> str | None:
    """
    Return "yesterday" or "day_before" if text contains a date marker, else None.
    Checks day_before first (more specific) then yesterday.
    """
    if _DAY_BEFORE_PATTERNS.search(text):
        return "day_before"
    if _YESTERDAY_PATTERNS.search(text):
        return "yesterday"
    return None


def _retro_date(marker: str) -> datetime:
    """Return the UTC datetime (midnight) for the given marker."""
    now = datetime.now(timezone.utc)
    offset = 1 if marker == "yesterday" else 2
    target = now - timedelta(days=offset)
    return target.replace(hour=0, minute=0, second=0, microsecond=0)


def _format_date_ru(dt: datetime) -> str:
    """Format date as 'DD месяца'."""
    months = {
        1: "января", 2: "февраля", 3: "марта", 4: "апреля",
        5: "мая", 6: "июня", 7: "июля", 8: "августа",
        9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
    }
    return f"{dt.day} {months[dt.month]}"


def _retro_confirm_kb(marker: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    label = "вчера" if marker == "yesterday" else "позавчера"
    builder.button(text=f"✅ Да, за {label}", callback_data=f"retro_yes:{marker}")
    builder.button(text="📅 Нет, за сегодня", callback_data="retro_no")
    builder.adjust(1)
    return builder.as_markup()


def _retro_choose_kb() -> InlineKeyboardMarkup:
    now = datetime.now(timezone.utc)
    yesterday_dt = now - timedelta(days=1)
    day_before_dt = now - timedelta(days=2)
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"Вчера, {_format_date_ru(yesterday_dt)}",
        callback_data="retro_choose:yesterday",
    )
    builder.button(
        text=f"Позавчера, {_format_date_ru(day_before_dt)}",
        callback_data="retro_choose:day_before",
    )
    builder.adjust(1)
    return builder.as_markup()


# ── /вчера command ─────────────────────────────────────────────────────────────

@router.message(OnboardingCompleted(), Command("retro"))
async def cmd_retro(message: Message, state: FSMContext) -> None:
    """Let user explicitly choose a retro date before entering a meal."""
    now = datetime.now(timezone.utc)
    yesterday_dt = now - timedelta(days=1)
    day_before_dt = now - timedelta(days=2)
    await message.answer(
        f"За какой день записать?\n\n"
        f"Я могу добавить запись за вчера ({_format_date_ru(yesterday_dt)}) "
        f"или позавчера ({_format_date_ru(day_before_dt)}).",
        reply_markup=_retro_choose_kb(),
    )


@router.callback_query(F.data.startswith("retro_choose:"))
async def retro_choose_callback(
    callback: CallbackQuery, state: FSMContext
) -> None:
    marker = callback.data.split(":")[1]  # "yesterday" or "day_before"
    retro_dt = _retro_date(marker)
    date_str = _format_date_ru(retro_dt)

    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)

    await state.set_state(RetroStates.awaiting_retro_meal)
    await state.update_data(retro_marker=marker)

    label = "вчера" if marker == "yesterday" else "позавчера"
    await callback.message.answer(
        f"Записываем за {label}, {date_str}.\n\n"
        f"Напиши, сфотографируй или надиктуй, что ел(а) — я добавлю в нужный день.\n\n"
        f"/cancel — отменить"
    )


# ── Date-marker interception (intercepts meal input when date marker found) ────

async def maybe_redirect_to_retro(
    message: Message,
    text: str,
    user: User,
    state: FSMContext,
) -> bool:
    """
    If text contains a date marker, ask for confirmation and save the
    original text in FSM state. Returns True if redirected.
    """
    marker = detect_retro_marker(text)
    if not marker:
        return False

    retro_dt = _retro_date(marker)
    date_str = _format_date_ru(retro_dt)
    label = "вчера" if marker == "yesterday" else "позавчера"

    await state.set_state(RetroStates.awaiting_date_confirm)
    await state.update_data(retro_marker=marker, retro_text=text)

    await message.answer(
        f"Я вижу, что это было {label}. Записать за {date_str}?",
        reply_markup=_retro_confirm_kb(marker),
    )
    return True


# ── Date confirm callbacks ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("retro_yes:"))
async def retro_yes_callback(
    callback: CallbackQuery,
    user: User,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    marker = callback.data.split(":")[1]
    data = await state.get_data()
    original_text: str = data.get("retro_text", "")
    retro_dt = _retro_date(marker)

    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.clear()

    date_str = _format_date_ru(retro_dt)

    if not original_text:
        await callback.message.answer(
            f"Записываем за {date_str}. Напиши, что ел(а)."
        )
        await state.set_state(RetroStates.awaiting_retro_meal)
        await state.update_data(retro_marker=marker)
        return

    # Run the meal pipeline with retro date
    await callback.message.answer(f"Записываю за {date_str}...")
    await _run_retro_pipeline(
        message=callback.message,
        text=original_text,
        retro_dt=retro_dt,
        user=user,
        db=db,
        date_str=date_str,
    )


@router.callback_query(F.data == "retro_no")
async def retro_no_callback(
    callback: CallbackQuery,
    user: User,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    """User chose 'No, save for today' — run pipeline normally."""
    data = await state.get_data()
    original_text: str = data.get("retro_text", "")
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.clear()

    if original_text:
        from bot.handlers.meal import run_meal_pipeline
        await run_meal_pipeline(
            message=callback.message,
            text=original_text,
            input_type=MealInputType.text,
            user=user,
            db=db,
        )


# ── Retro meal state: accept text/voice/photo ─────────────────────────────────

@router.message(
    OnboardingCompleted(),
    StateFilter(RetroStates.awaiting_retro_meal),
    F.text,
)
async def handle_retro_text(
    message: Message,
    user: User,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    data = await state.get_data()
    marker = data.get("retro_marker", "yesterday")
    retro_dt = _retro_date(marker)
    date_str = _format_date_ru(retro_dt)

    await state.clear()
    await _run_retro_pipeline(
        message=message,
        text=message.text.strip(),
        retro_dt=retro_dt,
        user=user,
        db=db,
        date_str=date_str,
    )


@router.message(
    OnboardingCompleted(),
    StateFilter(RetroStates.awaiting_retro_meal),
    F.voice,
)
async def handle_retro_voice(
    message: Message,
    user: User,
    state: FSMContext,
    db: AsyncSession,
    bot,
) -> None:
    import io
    from bot.ai.factory import get_stt_provider

    data = await state.get_data()
    marker = data.get("retro_marker", "yesterday")
    retro_dt = _retro_date(marker)
    date_str = _format_date_ru(retro_dt)

    try:
        file = await bot.get_file(message.voice.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        audio_bytes = buf.getvalue()
        buf.close()
    except Exception as exc:
        logger.error("retro_voice_download_failed", error=str(exc))
        await message.answer("Не удалось загрузить голосовое — попробуй написать текстом.")
        return

    stt = get_stt_provider()
    try:
        result = await stt.transcribe(audio_bytes, mime_type="audio/ogg")
        transcription = result.text.strip()
    except Exception as exc:
        logger.error("retro_voice_stt_failed", error=str(exc))
        await message.answer("Не удалось распознать голосовое — попробуй написать текстом.")
        return

    if not transcription:
        await message.answer("Не разобрал, что сказано. Напиши текстом.")
        return

    await state.clear()
    await _run_retro_pipeline(
        message=message,
        text=transcription,
        retro_dt=retro_dt,
        user=user,
        db=db,
        date_str=date_str,
    )


# ── Shared retro pipeline ─────────────────────────────────────────────────────

async def _run_retro_pipeline(
    message: Message,
    text: str,
    retro_dt: datetime,
    user: User,
    db: AsyncSession,
    date_str: str,
) -> None:
    """
    Analyze meal text and save with logged_at = retro_dt.
    Shows the standard meal result keyboard.
    Appends a 📅 retroactive label to the reply.
    """
    from bot.ai import get_text_provider
    from bot.keyboards.meal import meal_result_kb
    provider = get_text_provider()
    try:
        result = await provider.analyze_meal(text)
    except Exception as exc:
        logger.error("retro_analysis_failed", error=str(exc))
        await message.answer("Не смог обработать — попробуй ещё раз.")
        return

    if result.needs_clarification:
        clarification = result.clarification_prompt or "Не совсем понял. Опиши подробнее."
        await message.answer(clarification)
        return

    # Save with retro logged_at
    from bot.db.models import Meal

    items_json = [
        {
            "name": it.name,
            "portion_description": it.portion_description,
            "calories": it.calories,
            "protein_g": it.protein_g,
            "fat_g": it.fat_g,
            "carbs_g": it.carbs_g,
        }
        for it in result.items
    ]

    meal = Meal(
        user_id=user.id,
        input_type=MealInputType.text,
        raw_input=text,
        calories=result.total_calories,
        protein_g=result.total_protein_g,
        fat_g=result.total_fat_g,
        carbs_g=result.total_carbs_g,
        confidence=result.confidence,
        confidence_notes=result.confidence_notes,
        meal_items=items_json,
        is_confirmed=False,
        is_deleted=False,
        logged_at=retro_dt,
    )
    db.add(meal)
    await db.flush()

    # Recalculate aggregate for the RETRO day
    await recalculate_daily_aggregate(user.id, retro_dt.date(), db)

    # Get retro day aggregate for display
    from sqlalchemy import select as _select
    from bot.db.models import DailyAggregate
    agg_result = await db.execute(
        _select(DailyAggregate).where(
            DailyAggregate.user_id == user.id,
            DailyAggregate.date == retro_dt.date(),
        )
    )
    agg = agg_result.scalar_one_or_none()

    from bot.services.stats_service import format_meal_result
    response = format_meal_result(
        meal_calories=result.total_calories,
        meal_protein=result.total_protein_g,
        meal_fat=result.total_fat_g,
        meal_carbs=result.total_carbs_g,
        meal_items=meal.meal_items,
        agg=agg,
        user=user,
    )

    # Prepend retro date label
    retro_label = f"📅 Записано за {date_str}\n\n"
    await message.answer(
        retro_label + response,
        reply_markup=meal_result_kb(meal.id, show_clarify=False, show_augment=False),
    )
    logger.bind(telegram_id=user.telegram_id).info(
        "retro_meal_saved",
        meal_id=meal.id,
        retro_date=retro_dt.date().isoformat(),
    )
