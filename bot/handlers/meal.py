"""
Meal input handler — text flow + shared pipeline for voice/photo reuse.

Catches plain text messages from users who have completed onboarding
and are not in any active FSM state (or are in patch/correction state).

Pipeline (shared via run_meal_pipeline / save_and_reply_meal):
  text / transcription
    → TextProvider.analyze_meal(text)
    → needs_clarification? → ask to clarify, stop
    → save_and_reply_meal() → result message + keyboard
    → [✅ Верно]      → confirm meal, clear keyboard
    → [✏️ Исправить] → enter patch mode (delta update, NOT re-describe)
    → [🔍 Уточнить]  → enter patch mode with clarification hint (photo/low-conf)
    → [📊 Статистика] → show /stats inline

Patch mode (Task 2):
  awaiting_patch state stores meal_id in FSM data
    → user sends free-text correction
    → PatchProvider.patch_meal(current_items, user_message)
    → understood? → update meal in-place → show updated result
    → not understood? → ask to rephrase (keep state)

Duplicate detection (Task 5):
  after saving a new meal, check for another recent meal in the window
  if found + text similar → show "Это не повтор?" inline question
  → [Новый приём]    → dismiss, keep both
  → [Это повтор — удалить] → soft-delete the new meal
"""
from __future__ import annotations

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.ai import get_patch_provider, get_text_provider
from bot.ai.openai_patch import items_to_patch_input, patch_result_to_items
from bot.ai.schemas import ConfidenceLevel, MealAnalysisResult, MealItem
from bot.db.models import MealInputType, OnboardingState, User
from bot.keyboards.meal import meal_confirm_kb, meal_result_kb
from bot.services.meal_service import (
    MealSpec,
    confirm_meal,
    delete_meal,
    get_meal_by_id,
    get_recent_meal,
    get_today_aggregate,
    replace_meal,
    save_meal,
    update_meal,
)
from bot.services.stats_service import format_meal_result, format_stats
from bot.services.tip_service import maybe_get_tip  # shown in confirmation message
from config import settings

logger = structlog.get_logger(__name__)
router = Router(name="meal")

# ── FSM states ────────────────────────────────────────────────────────────────

class MealStates(StatesGroup):
    awaiting_correction = State()     # legacy: kept so old in-flight states don't break
    awaiting_patch = State()          # new delta-update mode
    awaiting_clarification = State()  # waiting for user's answer to a clarification question
    # awaiting_augment lives in meal_batch.AugmentStates to avoid circular imports


# ── Duplicate similarity helper ───────────────────────────────────────────────

def _jaccard(a: str | None, b: str | None) -> float:
    """Word-level Jaccard similarity between two strings."""
    if not a or not b:
        return 0.0
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    union = words_a | words_b
    if not union:
        return 0.0
    return len(words_a & words_b) / len(union)


# ── Filter: only users who finished onboarding ────────────────────────────────

class OnboardingCompleted:
    """Passes through only when user has completed onboarding and has targets set."""
    def __call__(self, message: Message, user: User) -> bool:
        return (
            user.onboarding_state == OnboardingState.completed
            and user.targets_set
        )


# ── Shared: save meal + build reply + duplicate check ─────────────────────────

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
    is_photo = input_type == MealInputType.photo
    response = format_meal_result(
        meal_calories=result.total_calories,
        meal_protein=result.total_protein_g,
        meal_fat=result.total_fat_g,
        meal_carbs=result.total_carbs_g,
        meal_items=meal.meal_items,
        agg=agg,
        user=user,
        is_photo=is_photo,
    )

    if disclaimer:
        response = f"<i>{disclaimer}</i>\n\n{response}"

    await message.answer(
        response,
        reply_markup=meal_result_kb(meal.id),
    )
    logger.bind(telegram_id=user.telegram_id).info(
        "meal_saved",
        meal_id=meal.id,
        input_type=input_type.value,
        confidence=result.confidence,
    )

    # ── Duplicate detection ───────────────────────────────────────────────────
    recent = await get_recent_meal(
        user_id=user.id,
        within_minutes=settings.meal_duplicate_window_minutes,
        exclude_meal_id=meal.id,
        db=db,
    )
    if recent is not None:
        similarity = _jaccard(raw_input, recent.raw_input)
        # Trigger if: texts are similar OR both have no raw_input (two photos in quick succession)
        is_suspicious = (
            similarity >= settings.meal_duplicate_similarity_threshold
            or (raw_input is None and recent.raw_input is None)
        )
        if is_suspicious:
            from bot.keyboards.meal import duplicate_check_kb  # avoid circular at module level
            import datetime as _dt
            local_time = recent.logged_at.strftime("%H:%M")
            await message.answer(
                f"<i>Похоже на приём в {local_time} — это не повтор?</i>",
                reply_markup=duplicate_check_kb(meal.id),
            )


# ── Shared: text/voice analysis pipeline ──────────────────────────────────────

async def run_meal_pipeline(
    message: Message,
    text: str,
    input_type: MealInputType,
    user: User,
    db: AsyncSession,
    *,
    force_commit: bool = False,
) -> None:
    """
    Text-based meal pipeline: analyze → clarify or save+reply.
    Reused by both text and voice handlers.

    force_commit=True: if LLM still returns needs_clarification despite force-commit
    instructions, save with low confidence instead of asking another question.
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
        if force_commit:
            if result.items:
                # Save with low confidence — stop the loop
                log.warning("force_commit_override_clarification", items=len(result.items))
                result = MealAnalysisResult(
                    items=result.items,
                    total_calories=result.total_calories,
                    total_protein_g=result.total_protein_g,
                    total_fat_g=result.total_fat_g,
                    total_carbs_g=result.total_carbs_g,
                    confidence=ConfidenceLevel.low,
                    confidence_notes="Записано с наилучшей оценкой",
                    needs_clarification=False,
                    clarification_prompt=None,
                )
            else:
                log.warning("force_commit_no_items_fallback")
                await message.answer(
                    "Не удалось распознать приём. Попробуй описать заново — "
                    "можно текстом, голосом или сфотографировать."
                )
                return
        else:
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


# ── Clarification answer handlers ────────────────────────────────────────────

@router.message(
    OnboardingCompleted(),
    StateFilter(MealStates.awaiting_clarification),
    F.text,
)
async def handle_clarification_text(
    message: Message,
    user: User,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    await _handle_clarification_answer(message, message.text.strip(), user, state, db)


@router.message(
    OnboardingCompleted(),
    StateFilter(MealStates.awaiting_clarification),
    F.voice,
)
async def handle_clarification_voice(
    message: Message,
    user: User,
    state: FSMContext,
    db: AsyncSession,
    bot,
) -> None:
    import io
    from bot.ai.factory import get_stt_provider
    try:
        file = await bot.get_file(message.voice.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        audio_bytes = buf.getvalue()
        buf.close()
    except Exception as exc:
        logger.error("clarification_voice_download_failed", error=str(exc))
        await message.answer("Не удалось загрузить голосовое — ответь текстом.")
        return

    stt = get_stt_provider()
    try:
        result = await stt.transcribe(audio_bytes, mime_type="audio/ogg")
        text = result.text.strip()
    except Exception as exc:
        logger.error("clarification_voice_stt_failed", error=str(exc))
        await message.answer("Не удалось распознать голосовое — ответь текстом.")
        return

    if not text:
        await message.answer("Не разобрал, что сказано. Ответь текстом.")
        return

    await _handle_clarification_answer(message, text, user, state, db)


async def _handle_clarification_answer(
    message: Message,
    user_answer: str,
    user: "User",
    state: FSMContext,
    db: "AsyncSession",
) -> None:
    """
    Process the user's answer to a clarification question.
    Combines original context + Q/A into a single LLM call.
    After MAX rounds, adds a force-commit instruction so the LLM stops asking.
    """
    data = await state.get_data()
    original_text: str = data.get("clarification_original_text") or ""
    clarification_question: str = data.get("clarification_question") or ""
    rounds: int = data.get("clarification_rounds", 1)

    await state.clear()

    # Build combined context — include everything the LLM needs
    parts: list[str] = []
    if original_text:
        parts.append(original_text)
    if clarification_question:
        parts.append(f"Уточняющий вопрос: {clarification_question}")
    parts.append(f"Ответ: {user_answer}")

    combined = "\n\n".join(parts)

    # If we've exhausted rounds, instruct the LLM to commit without asking again
    force_commit = rounds >= settings.max_clarification_rounds
    if force_commit:
        combined += (
            "\n\nВАЖНО: Это уже уточнённый ввод. "
            "Запиши приём пищи с наилучшей оценкой на основе всей информации выше. "
            "Не задавай дополнительных вопросов."
        )

    await run_meal_pipeline(
        message=message,
        text=combined,
        input_type=MealInputType.text,
        user=user,
        db=db,
        force_commit=force_commit,
    )


# ── Text meal handler (normal + legacy correction state) ──────────────────────

_RETRY_PHRASES = {
    "посчитай", "считай", "посчитать", "обработай", "обработать",
    "анализируй", "проанализируй", "пересчитай", "давай", "ну давай",
    "retry", "повтори", "повторить",
}


@router.message(
    OnboardingCompleted(),
    StateFilter(None, MealStates.awaiting_correction),
    F.text,
)
async def handle_text_meal(
    message: Message,
    user: User,
    bot: Bot,
    state: FSMContext,
) -> None:
    text = message.text.strip()

    # If message looks like a retry request and we have a saved photo — re-process it
    if text.lower().rstrip("!.") in _RETRY_PHRASES:
        data = await state.get_data()
        file_id = data.get("last_photo_file_id")
        if file_id:
            caption = data.get("last_photo_caption")
            await state.clear()
            try:
                import io
                file = await bot.get_file(file_id)
                buf = io.BytesIO()
                await bot.download_file(file.file_path, destination=buf)
                image_bytes = buf.getvalue()
                buf.close()
                from bot.services.debounce_service import BufferedMessage, meal_debounce_service
                await meal_debounce_service.add_message(
                    telegram_id=user.telegram_id,
                    chat_id=message.chat.id,
                    msg=BufferedMessage(
                        kind="photo",
                        image_bytes=image_bytes,
                        mime_type="image/jpeg",
                        caption=caption,
                    ),
                )
                return
            except Exception:
                await message.answer(
                    "Не могу найти то фото — пришли его ещё раз, пожалуйста."
                )
                return

    await state.clear()

    # Check for retro date markers (вчера / позавчера) — redirect if found
    from bot.handlers.retro import maybe_redirect_to_retro
    redirected = await maybe_redirect_to_retro(message, text, user, state)
    if redirected:
        return

    from bot.services.debounce_service import BufferedMessage, meal_debounce_service
    msg = BufferedMessage(kind="text", text=text)
    await meal_debounce_service.add_message(
        telegram_id=user.telegram_id,
        chat_id=message.chat.id,
        msg=msg,
    )


# ── Patch mode: shared logic ──────────────────────────────────────────────────

async def _apply_patch(
    correction_text: str,
    meal_id: int,
    user: User,
    message: Message,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    """
    Core patch logic shared by text and voice handlers.

    Always soft-deletes the old meal and inserts new one(s).
    Never mutates the original row — this preserves a full edit history.
    """
    meal = await get_meal_by_id(meal_id, db)
    if meal is None or meal.user_id != user.id or meal.is_deleted:
        await state.clear()
        await message.answer("Приём не найден. Опиши его заново.")
        return

    log = logger.bind(telegram_id=user.telegram_id, meal_id=meal_id)

    current_items = items_to_patch_input(meal.meal_items or [])
    if not current_items:
        # No structured items — fall back to re-describe from scratch.
        # Soft-delete the ghost meal first so the counter isn't doubled.
        await delete_meal(meal, db)
        await state.clear()
        await run_meal_pipeline(
            message=message,
            text=correction_text,
            input_type=MealInputType.text,
            user=user,
            db=db,
        )
        return

    provider = get_patch_provider()
    try:
        patch_result = await provider.patch_meal(
            current_items=current_items,
            user_message=correction_text,
        )
    except Exception as exc:
        log.error("meal_patch_failed", error=str(exc))
        await message.answer("Не смог применить правку — попробуй ещё раз.")
        return

    if not patch_result.understood:
        clarification = patch_result.clarification_prompt or (
            "Не понял, что именно исправить. Напиши точнее — "
            "например «сырники не 220, а 150г» или «убери кофе»."
        )
        await message.answer(clarification)
        return

    await state.clear()

    # Build specs for all resulting meals
    first_items = patch_result_to_items(patch_result.items)
    specs: list[MealSpec] = [
        MealSpec(
            items=first_items,
            calories=patch_result.total_calories,
            protein_g=patch_result.total_protein_g,
            fat_g=patch_result.total_fat_g,
            carbs_g=patch_result.total_carbs_g,
        )
    ]
    for extra in patch_result.extra_meals:
        extra_items = [
            {
                "name": it.name,
                "portion_description": it.portion_description,
                "calories": it.calories,
                "protein_g": it.protein_g,
                "fat_g": it.fat_g,
                "carbs_g": it.carbs_g,
            }
            for it in extra.items
        ]
        specs.append(
            MealSpec(
                items=extra_items,
                calories=extra.total_calories,
                protein_g=extra.total_protein_g,
                fat_g=extra.total_fat_g,
                carbs_g=extra.total_carbs_g,
            )
        )

    # Atomic: soft-delete old meal, insert new meal(s)
    try:
        new_meals = await replace_meal(old_meal=meal, specs=specs, db=db)
    except Exception as exc:
        log.error("meal_replace_failed", error=str(exc))
        await message.answer(
            "Не удалось применить исправление — попробуй ещё раз."
        )
        return

    new_ids = [m.id for m in new_meals]
    log.info(
        "meal_replaced",
        meal_id_before=meal_id,
        meal_ids_after=new_ids,
        split_count=len(new_meals),
    )

    # ── Split response ────────────────────────────────────────────────────────
    if len(new_meals) > 1:
        await message.answer(f"Разделил на {len(new_meals)} приёма — вот они:")

        for idx, (spec, new_meal) in enumerate(zip(specs, new_meals), start=1):
            agg = await get_today_aggregate(user.id, db)
            response = format_meal_result(
                meal_calories=spec.calories,
                meal_protein=spec.protein_g,
                meal_fat=spec.fat_g,
                meal_carbs=spec.carbs_g,
                meal_items=spec.items,
                agg=agg,
                user=user,
            )
            await message.answer(
                f"<b>Приём {idx}:</b>\n\n{response}",
                reply_markup=meal_result_kb(new_meal.id),
            )
        return

    # ── Regular patch: single meal replaced ──────────────────────────────────
    agg = await get_today_aggregate(user.id, db)
    response = format_meal_result(
        meal_calories=specs[0].calories,
        meal_protein=specs[0].protein_g,
        meal_fat=specs[0].fat_g,
        meal_carbs=specs[0].carbs_g,
        meal_items=first_items,
        agg=agg,
        user=user,
    )
    await message.answer(
        f"Исправил!\n\n{response}",
        reply_markup=meal_result_kb(new_meals[0].id),
    )


# ── Patch mode: text correction ───────────────────────────────────────────────

@router.message(
    OnboardingCompleted(),
    StateFilter(MealStates.awaiting_patch),
    F.text,
    ~F.text.startswith("/"),
)
async def handle_patch_text(
    message: Message,
    user: User,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    text = message.text.strip()

    # "Нет" / "не" / "ничего" в patch mode — пользователь хочет выйти
    if text.lower() in {"нет", "не", "ничего", "ничего не менять", "всё верно", "все верно", "no", "n"}:
        await state.clear()
        await message.answer("Хорошо, ничего не меняю. Приём сохранён как есть.")
        return

    data = await state.get_data()
    meal_id = data.get("patch_meal_id")
    if not meal_id:
        await state.clear()
        await message.answer("Что-то пошло не так. Попробуй описать приём заново.")
        return
    await _apply_patch(
        correction_text=text,
        meal_id=meal_id,
        user=user,
        message=message,
        state=state,
        db=db,
    )


# ── Patch mode: voice correction ─────────────────────────────────────────────

@router.message(
    OnboardingCompleted(),
    StateFilter(MealStates.awaiting_patch),
    F.voice,
)
async def handle_patch_voice(
    message: Message,
    user: User,
    state: FSMContext,
    db: AsyncSession,
    bot,
) -> None:
    """Handle voice corrections in patch mode (transcribe → patch inline)."""
    from bot.ai.factory import get_stt_provider
    import io

    data = await state.get_data()
    meal_id = data.get("patch_meal_id")
    if not meal_id:
        await state.clear()
        await message.answer("Что-то пошло не так. Попробуй описать приём заново.")
        return

    log = logger.bind(telegram_id=user.telegram_id, meal_id=meal_id)

    try:
        file = await bot.get_file(message.voice.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        audio_bytes = buf.getvalue()
        buf.close()
    except Exception as exc:
        log.error("patch_voice_download_failed", error=str(exc))
        await message.answer(
            "Не удалось загрузить голосовое — попробуй написать текстом, что изменить."
        )
        return

    stt = get_stt_provider()
    try:
        result = await stt.transcribe(audio_bytes, mime_type="audio/ogg")
        transcription = result.text.strip()
    except Exception as exc:
        log.error("patch_voice_stt_failed", error=str(exc))
        await message.answer(
            "Не удалось распознать голосовое — попробуй написать текстом, что изменить."
        )
        return

    if not transcription:
        await message.answer("Не разобрал, что сказано. Напиши текстом, что изменить.")
        return

    log.info("patch_voice_transcribed", chars=len(transcription))
    await _apply_patch(
        correction_text=transcription,
        meal_id=meal_id,
        user=user,
        message=message,
        state=state,
        db=db,
    )


# ── /retry — re-process last photo ───────────────────────────────────────────

@router.message(OnboardingCompleted(), Command("retry"))
async def handle_retry(
    message: Message,
    user: User,
    bot: Bot,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    file_id = data.get("last_photo_file_id")
    if not file_id:
        await message.answer("Нет сохранённого фото для повтора. Пришли фото заново.")
        return

    caption = data.get("last_photo_caption")
    await state.clear()

    try:
        file = await bot.get_file(file_id)
        import io
        buf = io.BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        image_bytes = buf.getvalue()
        buf.close()
    except Exception:
        await message.answer("Не удалось загрузить фото — возможно, оно устарело. Пришли заново.")
        return

    from bot.services.debounce_service import BufferedMessage, meal_debounce_service
    await meal_debounce_service.add_message(
        telegram_id=user.telegram_id,
        chat_id=message.chat.id,
        msg=BufferedMessage(
            kind="photo",
            image_bytes=image_bytes,
            mime_type="image/jpeg",
            caption=caption,
        ),
    )
    await message.answer("Анализирую фото ещё раз…")


# ── /cancel — universal state clear ──────────────────────────────────────────

@router.message(Command("cancel"))
async def handle_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    if current:
        await message.answer("Отменено.")
    else:
        await message.answer("Нечего отменять.")


# ── Patch mode: non-text message guard ────────────────────────────────────────

@router.message(StateFilter(MealStates.awaiting_patch))
async def handle_patch_non_text(message: Message) -> None:
    """Remind user that patch mode only accepts text."""
    await message.answer(
        "В режиме исправления напиши текстом, что именно изменить — "
        "например «убери кофе» или «сырники не 220, а 150г».\n\n"
        "Или нажми /cancel для отмены."
    )


# ── ✅ Confirm ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("meal_ok:"))
async def meal_confirm_callback(
    callback: CallbackQuery, user: User, db: AsyncSession
) -> None:
    await callback.answer("Записано ✅")
    meal_id = int(callback.data.split(":")[1])
    meal = await get_meal_by_id(meal_id, db)

    if not (meal and meal.user_id == user.id and not meal.is_deleted):
        await callback.message.edit_reply_markup(reply_markup=None)
        return

    await confirm_meal(meal, db)

    # Remove keyboard from the meal card — it stays clean
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Maybe get a periodic tip (increments counter, no extra LLM call)
    tip = await maybe_get_tip(user, db)

    # Build confirmation message (separate from meal card)
    confirm_parts = ["Записано ✅"]
    if tip:
        confirm_parts.append(f"\n{tip}")

    await callback.message.answer(
        "\n".join(confirm_parts),
        reply_markup=meal_confirm_kb(meal_id),
    )


# ── 🎯 Detailed meal assessment (LLM nutritionist) ────────────────────────────

@router.callback_query(F.data.startswith("meal_detail:"))
async def meal_detail_callback(
    callback: CallbackQuery, user: User, db: AsyncSession
) -> None:
    """Generate and send a full nutritionist assessment for a confirmed meal."""
    await callback.answer()
    meal_id = int(callback.data.split(":")[1])
    meal = await get_meal_by_id(meal_id, db)

    if not (meal and meal.user_id == user.id):
        await callback.message.edit_reply_markup(reply_markup=None)
        return

    # Remove button immediately so user can't tap twice
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    agg = await get_today_aggregate(user.id, db)

    # Build context string for LLM
    items_lines: list[str] = []
    for it in meal.meal_items or []:
        items_lines.append(
            f"- {it.get('name')} ({it.get('portion_description')}): "
            f"{int(it.get('calories', 0))} ккал, "
            f"Б {it.get('protein_g', 0):.0f}г, "
            f"Ж {it.get('fat_g', 0):.0f}г, "
            f"У {it.get('carbs_g', 0):.0f}г"
        )
    items_text = "\n".join(items_lines) if items_lines else f"Итого: {int(meal.calories)} ккал"

    t_kcal = user.daily_calories_target or 0
    t_prot = user.daily_protein_g_target or 0
    t_fat = user.daily_fat_g_target or 0
    t_carbs = user.daily_carbs_g_target or 0

    c_kcal = agg.total_calories
    c_prot = agg.total_protein_g
    c_fat = agg.total_fat_g
    c_carbs = agg.total_carbs_g

    def _pct(val: float, target: float) -> str:
        return f"{int(val / target * 100)}%" if target > 0 else "—"

    rem_kcal = t_kcal - c_kcal
    rem_prot = t_prot - c_prot
    rem_fat = t_fat - c_fat
    rem_carbs = t_carbs - c_carbs

    meal_pct = f"{int(meal.calories / t_kcal * 100)}%" if t_kcal > 0 else "—"

    user_context = (
        f"ДАННЫЕ ПРИЁМА:\n{items_text}\n\n"
        f"Итого приёма: {int(meal.calories)} ккал ({meal_pct} от дневной цели), "
        f"Б {meal.protein_g:.0f}г, Ж {meal.fat_g:.0f}г, У {meal.carbs_g:.0f}г\n\n"
        f"ДНЕВНЫЕ ДАННЫЕ (после этого приёма):\n"
        f"Съедено: {int(c_kcal)} ккал из {int(t_kcal)} ({_pct(c_kcal, t_kcal)})\n"
        f"Б: {c_prot:.0f}г из {t_prot:.0f}г ({_pct(c_prot, t_prot)}) · "
        f"Ж: {c_fat:.0f}г из {t_fat:.0f}г ({_pct(c_fat, t_fat)}) · "
        f"У: {c_carbs:.0f}г из {t_carbs:.0f}г ({_pct(c_carbs, t_carbs)})\n"
        f"Осталось: {int(rem_kcal)} ккал · Б {rem_prot:.0f}г · Ж {rem_fat:.0f}г · У {rem_carbs:.0f}г"
    )

    try:
        from pathlib import Path
        prompt_path = Path(__file__).parent.parent / "prompts" / "meal_rating_detail.txt"
        system_prompt = prompt_path.read_text(encoding="utf-8").strip()

        from bot.ai.factory import _openai_client
        client = _openai_client()
        response = await client.chat.completions.create(
            model=settings.text_model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_context},
            ],
            temperature=0.3,
            max_tokens=300,
        )
        detail_text = response.choices[0].message.content.strip()
        await callback.message.answer(detail_text)
    except Exception as exc:
        logger.error("meal_detail_failed", error=str(exc), meal_id=meal_id)
        await callback.message.answer(
            "Не удалось получить оценку — попробуй позже."
        )


# ── ✏️ Patch (delta update) ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("meal_fix:"))
async def meal_fix_callback(
    callback: CallbackQuery,
    user: User,
    state: FSMContext,
) -> None:
    await callback.answer()
    meal_id = int(callback.data.split(":")[1])

    await state.set_state(MealStates.awaiting_patch)
    await state.update_data(patch_meal_id=meal_id)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "Что именно исправить? Напиши свободным текстом — например:\n"
        "· «сырники не 220, а 150г»\n"
        "· «убери кофе»\n"
        "· «добавь ложку сметаны»\n"
        "· «исправь весь приём» + новый состав\n\n"
        "/cancel — отменить."
    )


# ── 📊 Inline stats ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "meal_stats")
async def meal_stats_callback(
    callback: CallbackQuery, user: User, db: AsyncSession
) -> None:
    await callback.answer()
    agg = await get_today_aggregate(user.id, db)
    await callback.message.answer(format_stats(agg, user))


# ── Duplicate detection callbacks ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("meal_delete:"))
async def meal_delete_callback(
    callback: CallbackQuery, user: User, db: AsyncSession
) -> None:
    await callback.answer("Удалено 🗑")
    meal_id = int(callback.data.split(":")[1])
    meal = await get_meal_by_id(meal_id, db)
    if meal and meal.user_id == user.id and not meal.is_deleted:
        await delete_meal(meal, db)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("Приём удалён.")


@router.callback_query(F.data.startswith("dedup_ok:"))
async def dedup_ok_callback(callback: CallbackQuery) -> None:
    """User confirmed it's a new (separate) meal — do nothing, just dismiss."""
    await callback.answer("Ок, оставил как новый приём.")
    await callback.message.delete()


@router.callback_query(F.data.startswith("dedup_delete:"))
async def dedup_delete_callback(
    callback: CallbackQuery, user: User, db: AsyncSession
) -> None:
    """User says it was a duplicate — soft-delete the newer meal."""
    await callback.answer("Удалил повторный приём.")
    meal_id = int(callback.data.split(":")[1])
    meal = await get_meal_by_id(meal_id, db)
    if meal and meal.user_id == user.id and not meal.is_deleted:
        await delete_meal(meal, db)
    await callback.message.delete()


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
