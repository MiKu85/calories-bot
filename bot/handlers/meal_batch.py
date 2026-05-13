"""
Batch meal flush handler + augment / merge callbacks — Task 5.

flush_meal_buffer()
  Called by MealDebounceService when the debounce timer fires.
  Orchestrates: STT await → BatchMessage assembly → LLM batch call →
  DB save → Telegram response per meal.

Augment (➕ Дополнить)
  User can add missed food items to a recently-logged meal within
  MEAL_AUGMENT_WINDOW_MINUTES. Accepted input: text or voice (not a new photo).
  New items are analysed and appended to existing meal_items; totals recalculated.

Merge (🔗 Объединить с предыдущим)
  When the LLM splits a single debounce batch into multiple meals and the user
  disagrees, this callback merges the second meal's items into the first and
  soft-deletes the second.
"""
from __future__ import annotations

import asyncio
import io
import random
from datetime import datetime, timedelta, timezone

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.ai.factory import get_batch_provider, get_stt_provider, get_text_provider
from bot.ai.openai_batch import BatchMessage
from bot.ai.schemas import ConfidenceLevel, MealAnalysisResult
from bot.db.models import MealInputType, User
from bot.db.session import AsyncSessionLocal
from bot.handlers.meal import MealStates, OnboardingCompleted
from bot.keyboards.meal import meal_result_kb
from bot.services.debounce_service import BufferedMessage
from bot.services.meal_service import (
    delete_meal,
    get_meal_by_id,
    get_today_aggregate,
    save_meal,
    update_meal,
)
from bot.services.stats_service import format_meal_result
from config import settings

logger = structlog.get_logger(__name__)
router = Router(name="meal_batch")

_MIN_STT_LENGTH = 3
_LOADING_EMOJIS = ["🍕", "🍔", "🥗", "🥣", "🥝", "🌮", "🧁", "🩵", "🍱", "🥩", "🍜", "🌾"]


# ── Flush callback (called by MealDebounceService) ────────────────────────────

async def flush_meal_buffer(
    telegram_id: int,
    chat_id: int,
    messages: list[BufferedMessage],
) -> None:
    """
    Process a closed debounce buffer for one user.

    Creates its own DB session (runs outside any handler context).
    """
    log = logger.bind(telegram_id=telegram_id, msg_count=len(messages))

    # ── 1. Get user from DB ────────────────────────────────────────────────────
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user is None:
            log.warning("flush_user_not_found")
            return

        # ── 2. Await pending STT tasks ─────────────────────────────────────────
        # STT tasks were kicked off the moment voice messages arrived (parallel
        # to buffering). By the time the timer fires they are usually done.
        for msg in messages:
            if msg.kind == "voice" and msg.stt_task is not None:
                try:
                    if not msg.stt_task.done():
                        await asyncio.wait_for(msg.stt_task, timeout=15.0)
                except (asyncio.TimeoutError, Exception) as exc:
                    log.warning("stt_await_failed", error=str(exc))
                # stt_result was set inside the task; None means it failed

        # ── 3. Build BatchMessage list ─────────────────────────────────────────
        has_photo = any(m.kind == "photo" for m in messages)
        first_ts = messages[0].timestamp if messages else 0.0
        batch: list[BatchMessage] = []

        for msg in messages:
            delay = msg.timestamp - first_ts
            if msg.kind == "photo":
                batch.append(BatchMessage(
                    kind="photo",
                    delay_seconds=delay,
                    image_bytes=msg.image_bytes,
                    mime_type=msg.mime_type,
                    caption=msg.caption,
                ))
            elif msg.kind == "voice":
                transcript = msg.stt_result
                if not transcript or len(transcript) < _MIN_STT_LENGTH:
                    log.info("stt_transcript_too_short_skipped")
                    continue
                batch.append(BatchMessage(
                    kind="voice_transcript",
                    delay_seconds=delay,
                    text=transcript,
                ))
            elif msg.kind == "text" and msg.text:
                batch.append(BatchMessage(
                    kind="text",
                    delay_seconds=delay,
                    text=msg.text,
                ))

        if not batch:
            log.warning("flush_empty_batch_after_filter")
            return

        log.info("flush_batch_built", types=[b.kind for b in batch])

        # ── 4. Call batch LLM ─────────────────────────────────────────────────
        from bot.services.debounce_service import meal_debounce_service
        bot = meal_debounce_service._bot  # type: ignore[attr-defined]

        # Send loading indicator; delete it when the result is ready
        loading_msg_id: int | None = None
        try:
            loading_emoji = random.choice(_LOADING_EMOJIS)
            sent = await bot.send_message(chat_id, loading_emoji)
            loading_msg_id = sent.message_id
        except Exception:
            pass

        provider = get_batch_provider()
        try:
            results = await provider.analyze_meal_batch(batch)
        except Exception as exc:
            log.error("batch_llm_failed", error=str(exc))
            if loading_msg_id is not None:
                try:
                    await bot.delete_message(chat_id, loading_msg_id)
                except Exception:
                    pass
            await bot.send_message(
                chat_id,
                "Не смог обработать запрос — попробуй ещё раз или опиши приём заново.",
            )
            return

        if loading_msg_id is not None:
            try:
                await bot.delete_message(chat_id, loading_msg_id)
            except Exception:
                pass

        log.info(
            "flush_llm_done",
            meal_count=len(results),
            multi_meal=len(results) > 1,
        )

        # ── 5. Determine input_type for DB ────────────────────────────────────
        if has_photo:
            input_type = MealInputType.photo
        elif any(m.kind == "voice" for m in messages):
            input_type = MealInputType.voice
        else:
            input_type = MealInputType.text

        # Collect raw text inputs for dedup / raw_input field
        text_parts = [
            m.stt_result or m.text
            for m in messages
            if m.kind in ("text", "voice") and (m.stt_result or m.text)
        ]
        raw_input: str | None = " | ".join(text_parts) if text_parts else None

        # Caption from first photo (if any)
        first_caption = next(
            (m.caption for m in messages if m.kind == "photo" and m.caption), None
        )

        # ── 6. Save and reply for each meal ───────────────────────────────────
        multi = len(results) > 1
        saved_meal_ids: list[int] = []

        if multi:
            # Inform user that the LLM detected multiple meals
            await bot.send_message(
                chat_id,
                f"Нашёл {len(results)} разных приёма пищи. "
                "Если это был один — нажми «🔗 Объединить с предыдущим» под любым.",
            )

        for idx, result in enumerate(results):
            disclaimer: str | None = None

            if result.needs_clarification:
                prompt = result.clarification_prompt or (
                    "Не смог точно определить блюдо. "
                    "Опиши, что ел(а), текстом или голосом."
                )
                if result.confidence_notes:
                    prompt = f"{prompt}\n\n<i>Причина: {result.confidence_notes}</i>"

                # Save clarification context in FSM so the answer has full context
                fsm_storage = meal_debounce_service._fsm_storage
                if fsm_storage is not None:
                    from aiogram.fsm.context import FSMContext
                    from aiogram.fsm.storage.base import StorageKey
                    from bot.handlers.meal import MealStates
                    key = StorageKey(bot_id=bot.id, chat_id=chat_id, user_id=telegram_id)
                    fsm = FSMContext(storage=fsm_storage, key=key)
                    await fsm.set_state(MealStates.awaiting_clarification)
                    await fsm.update_data(
                        clarification_original_text=raw_input or first_caption,
                        clarification_question=prompt,
                        clarification_rounds=1,
                    )

                await bot.send_message(chat_id, prompt, parse_mode="HTML")
                continue


            meal = await save_meal(
                user=user,
                input_type=input_type,
                raw_input=raw_input or first_caption,
                result=result,
                db=db,
            )
            saved_meal_ids.append(meal.id)

            agg = await get_today_aggregate(user.id, db)
            response = format_meal_result(
                meal_calories=result.total_calories,
                meal_protein=result.total_protein_g,
                meal_fat=result.total_fat_g,
                meal_carbs=result.total_carbs_g,
                meal_items=meal.meal_items,
                agg=agg,
                user=user,
                is_photo=has_photo,
            )
            if disclaimer:
                response = f"<i>{disclaimer}</i>\n\n{response}"

            # "Объединить" button only on 2nd+ meal in a split batch
            prev_id = saved_meal_ids[idx - 1] if multi and idx > 0 else None

            await bot.send_message(
                chat_id,
                response,
                parse_mode="HTML",
                reply_markup=meal_result_kb(meal.id, prev_meal_id=prev_id),
            )

            log.info(
                "meal_saved_from_batch",
                meal_id=meal.id,
                idx=idx,
                confidence=result.confidence,
            )

        # ── 7. Commit ─────────────────────────────────────────────────────────
        # flush_meal_buffer runs outside any handler/middleware context, so
        # the DB middleware does not commit for us — must do it explicitly.
        await db.commit()


# ── Augment FSM states ────────────────────────────────────────────────────────

class AugmentStates(StatesGroup):
    awaiting_augment = State()  # waiting for user's text/voice to add to a meal


# ── ➕ Дополнить callback ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("meal_augment:"))
async def meal_augment_callback(
    callback: CallbackQuery,
    user: User,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    meal_id = int(callback.data.split(":")[1])
    meal = await get_meal_by_id(meal_id, db)

    if meal is None or meal.user_id != user.id or meal.is_deleted:
        await callback.answer("Приём не найден.", show_alert=True)
        return

    # Check augment window
    window = timedelta(minutes=settings.meal_augment_window_minutes)
    if datetime.now(timezone.utc) - meal.logged_at > window:
        await callback.answer()
        await callback.message.answer(
            "Окно дополнения закрыто — приём уже зафиксирован. "
            "Используй /history для редактирования или опиши новый приём."
        )
        return

    await callback.answer()
    await state.set_state(AugmentStates.awaiting_augment)
    await state.update_data(augment_meal_id=meal_id)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "Что ещё было в этом приёме? Напиши текстом или отправь голосовое.\n\n"
        "/cancel — отменить."
    )


# ── Augment: text input ───────────────────────────────────────────────────────

@router.message(
    OnboardingCompleted(),
    StateFilter(AugmentStates.awaiting_augment),
    F.text,
)
async def handle_augment_text(
    message: Message,
    user: User,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    await _apply_augment(
        message=message,
        text=message.text.strip(),
        user=user,
        state=state,
        db=db,
    )


# ── Augment: voice input ──────────────────────────────────────────────────────

@router.message(
    OnboardingCompleted(),
    StateFilter(AugmentStates.awaiting_augment),
    F.voice,
)
async def handle_augment_voice(
    message: Message,
    user: User,
    bot: Bot,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    # Download + transcribe
    try:
        file = await bot.get_file(message.voice.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        audio_bytes = buf.getvalue()
    except Exception as exc:
        logger.error("augment_voice_download_failed", error=str(exc))
        await message.answer("Не удалось загрузить голосовое. Попробуй ещё раз или напиши текстом.")
        return

    stt = get_stt_provider()
    try:
        transcription = await stt.transcribe(audio_bytes, mime_type="audio/ogg")
    except Exception as exc:
        logger.error("augment_stt_failed", error=str(exc))
        await message.answer("Не смог распознать голосовое. Попробуй ещё раз или напиши текстом.")
        return
    finally:
        del audio_bytes
        buf.close()

    text = transcription.text.strip()
    if len(text) < _MIN_STT_LENGTH:
        await message.answer(
            "Не расслышал — слишком короткое. Попробуй ещё раз или напиши текстом."
        )
        return

    await message.answer(f"<i>Распознал: «{text}»</i>")
    await _apply_augment(message=message, text=text, user=user, state=state, db=db)


# ── Augment: non-text/voice guard ─────────────────────────────────────────────

@router.message(StateFilter(AugmentStates.awaiting_augment))
async def handle_augment_non_text(message: Message) -> None:
    await message.answer(
        "В режиме дополнения отправь текст или голосовое с описанием того, что забыл(а) добавить.\n\n"
        "/cancel — отменить."
    )


# ── Shared augment apply logic ────────────────────────────────────────────────

async def _apply_augment(
    message: Message,
    text: str,
    user: User,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    data = await state.get_data()
    meal_id = data.get("augment_meal_id")
    if not meal_id:
        await state.clear()
        await message.answer("Что-то пошло не так. Опиши приём заново.")
        return

    meal = await get_meal_by_id(meal_id, db)
    if meal is None or meal.user_id != user.id or meal.is_deleted:
        await state.clear()
        await message.answer("Приём не найден. Опиши его заново.")
        return

    log = logger.bind(telegram_id=user.telegram_id, meal_id=meal_id)

    # Analyse the new addition as a standalone food description
    provider = get_text_provider()
    try:
        result = await provider.analyze_meal(text)
    except Exception as exc:
        log.error("augment_analysis_failed", error=str(exc))
        await message.answer("Не смог обработать — попробуй ещё раз.")
        return

    if result.needs_clarification:
        clarification = result.clarification_prompt or (
            "Не совсем понял. Опиши подробнее — что именно и примерный объём?"
        )
        await message.answer(clarification)
        return  # keep state — user can retry

    # Merge new items into existing meal
    existing_items: list[dict] = list(meal.meal_items or [])
    new_items = [
        {
            "name": item.name,
            "portion_description": item.portion_description,
            "calories": item.calories,
            "protein_g": item.protein_g,
            "fat_g": item.fat_g,
            "carbs_g": item.carbs_g,
        }
        for item in result.items
    ]
    merged_items = existing_items + new_items

    totals = {
        "calories": meal.calories + result.total_calories,
        "protein_g": meal.protein_g + result.total_protein_g,
        "fat_g": meal.fat_g + result.total_fat_g,
        "carbs_g": meal.carbs_g + result.total_carbs_g,
    }

    await update_meal(meal, merged_items, totals, db)
    await state.clear()
    log.info("augment_applied", new_items=len(new_items), total_items=len(merged_items))

    agg = await get_today_aggregate(user.id, db)
    response = format_meal_result(
        meal_calories=totals["calories"],
        meal_protein=totals["protein_g"],
        meal_fat=totals["fat_g"],
        meal_carbs=totals["carbs_g"],
        meal_items=merged_items,
        agg=agg,
        user=user,
    )
    await message.answer(
        f"Добавил!\n\n{response}",
        reply_markup=meal_result_kb(meal.id),
    )


# ── 🔗 Объединить с предыдущим ────────────────────────────────────────────────

@router.callback_query(F.data.startswith("meal_merge:"))
async def meal_merge_callback(
    callback: CallbackQuery,
    user: User,
    db: AsyncSession,
) -> None:
    """
    Merge `this_meal` into `prev_meal`:
      • append this_meal.meal_items → prev_meal.meal_items
      • add totals
      • soft-delete this_meal
    """
    parts = callback.data.split(":")
    prev_meal_id = int(parts[1])
    this_meal_id = int(parts[2])

    prev_meal = await get_meal_by_id(prev_meal_id, db)
    this_meal = await get_meal_by_id(this_meal_id, db)

    if (
        prev_meal is None or prev_meal.user_id != user.id or prev_meal.is_deleted
        or this_meal is None or this_meal.user_id != user.id or this_meal.is_deleted
    ):
        await callback.answer("Не удалось объединить — приём уже изменён.", show_alert=True)
        return

    merged_items = list(prev_meal.meal_items or []) + list(this_meal.meal_items or [])
    totals = {
        "calories": prev_meal.calories + this_meal.calories,
        "protein_g": prev_meal.protein_g + this_meal.protein_g,
        "fat_g": prev_meal.fat_g + this_meal.fat_g,
        "carbs_g": prev_meal.carbs_g + this_meal.carbs_g,
    }

    await update_meal(prev_meal, merged_items, totals, db)
    await delete_meal(this_meal, db)

    logger.bind(telegram_id=user.telegram_id).info(
        "meals_merged", prev_id=prev_meal_id, deleted_id=this_meal_id
    )

    await callback.answer("Объединил!")
    # Remove keyboard from the second meal message (this one)
    await callback.message.edit_reply_markup(reply_markup=None)

    agg = await get_today_aggregate(user.id, db)
    response = format_meal_result(
        meal_calories=totals["calories"],
        meal_protein=totals["protein_g"],
        meal_fat=totals["fat_g"],
        meal_carbs=totals["carbs_g"],
        meal_items=merged_items,
        agg=agg,
        user=user,
    )
    await callback.message.answer(
        f"Объединил в один приём!\n\n{response}",
        reply_markup=meal_result_kb(prev_meal.id),
    )
