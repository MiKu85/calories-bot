"""
Feedback flow — one-time per user, triggered 7 days after first meal.

States:
  awaiting_comment  — user chose "Написать комментарий", waiting for free text
  awaiting_voice    — feedback saved, now asking for optional voice comment

Flow A (quick option):
  [Всё нравится / Неточно считает / Неудобно] callback
    → save FeedbackRecord(feedback_text=option_label)
    → ask for voice → FSM: awaiting_voice

Flow B (text comment):
  [Написать комментарий] callback
    → ask to write → FSM: awaiting_comment
    → text received → save FeedbackRecord(feedback_text=text)
    → ask for voice → FSM: awaiting_voice

Voice step (both flows):
  voice message when FSM = awaiting_voice
    → FeedbackRecord.has_voice_comment = True
    → thank user → clear FSM
  [Пропустить] callback when FSM = awaiting_voice
    → thank user → clear FSM
"""
from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import FeedbackRecord, User
from bot.keyboards.feedback import feedback_options_kb, feedback_skip_voice_kb

logger = structlog.get_logger(__name__)
router = Router(name="feedback")

_FEEDBACK_REQUEST = (
    "Привет! Ты пользуешься ботом уже неделю — как впечатления?\n\n"
    "Выбери один вариант или напиши комментарий:"
)

_OPTION_LABELS = {
    "like": "Всё нравится",
    "inaccurate": "Иногда неточно считает",
    "inconvenient": "Неудобно пользоваться",
}

_ASK_VOICE = (
    "Спасибо! Если хочешь — запиши голосовой комментарий, это очень поможет.\n"
    "Или нажми «Пропустить»."
)

_THANKS = "Спасибо за обратную связь — это очень помогает улучшать бота!"


class FeedbackStates(StatesGroup):
    awaiting_comment = State()
    awaiting_voice = State()


# ── Quick option callbacks ─────────────────────────────────────────────────────

@router.callback_query(F.data.in_({"fb:like", "fb:inaccurate", "fb:inconvenient"}))
async def process_quick_feedback(
    callback: CallbackQuery, user: User, state: FSMContext, db: AsyncSession
) -> None:
    await callback.answer()
    option_key = callback.data.split(":")[1]
    label = _OPTION_LABELS[option_key]

    await _save_feedback(user, label, db)
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(FeedbackStates.awaiting_voice)
    await callback.message.answer(_ASK_VOICE, reply_markup=feedback_skip_voice_kb())
    logger.info("feedback_received", telegram_id=user.telegram_id, option=option_key)


# ── "Write comment" option ────────────────────────────────────────────────────

@router.callback_query(F.data == "fb:comment")
async def process_comment_option(
    callback: CallbackQuery, state: FSMContext
) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(FeedbackStates.awaiting_comment)
    await callback.message.answer("Напиши свой комментарий:")


@router.message(FeedbackStates.awaiting_comment, F.text)
async def process_comment_text(
    message: Message, user: User, state: FSMContext, db: AsyncSession
) -> None:
    await _save_feedback(user, message.text.strip(), db)
    await state.set_state(FeedbackStates.awaiting_voice)
    await message.answer(_ASK_VOICE, reply_markup=feedback_skip_voice_kb())
    logger.info("feedback_comment_received", telegram_id=user.telegram_id)


# ── Voice comment ─────────────────────────────────────────────────────────────

@router.message(FeedbackStates.awaiting_voice, F.voice)
async def process_feedback_voice(
    message: Message, user: User, state: FSMContext, db: AsyncSession
) -> None:
    await _mark_voice(user, db)
    await state.clear()
    await message.answer(_THANKS)
    logger.info("feedback_voice_received", telegram_id=user.telegram_id)


@router.callback_query(FeedbackStates.awaiting_voice, F.data == "fb:skip_voice")
async def skip_feedback_voice(
    callback: CallbackQuery, state: FSMContext
) -> None:
    await callback.answer()
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(_THANKS)


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _save_feedback(user: User, text: str, db: AsyncSession) -> FeedbackRecord:
    # FeedbackRecord is UNIQUE on user_id — upsert via get-or-create
    result = await db.execute(
        select(FeedbackRecord).where(FeedbackRecord.user_id == user.id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        record = FeedbackRecord(user_id=user.id, feedback_text=text)
        db.add(record)
    else:
        record.feedback_text = text
    await db.flush()
    return record


async def _mark_voice(user: User, db: AsyncSession) -> None:
    result = await db.execute(
        select(FeedbackRecord).where(FeedbackRecord.user_id == user.id)
    )
    record = result.scalar_one_or_none()
    if record:
        record.has_voice_comment = True
        await db.flush()
