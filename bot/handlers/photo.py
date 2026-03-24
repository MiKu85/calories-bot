"""
Food photo analysis handler.

Flow:
  photo message (optionally with caption as user hint)
    → download highest-resolution Telegram photo (in-memory, never persisted)
    → VisionProvider.analyze_meal_photo(bytes, mime_type, user_hint)
    → needs_clarification? → explain + ask to describe by text/voice
    → confidence LOW but clarification not triggered?
        → save with low-confidence note + cautious disclaimer in reply
    → confidence MEDIUM/HIGH → save + standard reply
    → same keyboard as text/voice: ✅ Верно · ✏️ Исправить · 📊 Статистика

Honesty rules (per product spec):
  - Bot MUST say it is not sure when confidence is low.
  - Bot MUST NOT present photo estimates as medically reliable.
  - Photo disclaimer is always shown regardless of confidence level.
  - When needs_clarification=True, bot MUST ask for clarification; MUST NOT save.

Error / fallback scenarios:
  1. Photo download fails          → ask to try again or describe by text
  2. Vision provider error (3 retry) → ask to describe by text or voice
  3. Invalid / unparseable response  → handled inside provider → needs_clarification=True
  4. No food visible in photo        → provider sets needs_clarification=True with explanation
  5. Multiple dishes, too uncertain  → provider sets needs_clarification=True
"""
from __future__ import annotations

import io

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.ai import get_vision_provider
from bot.ai.schemas import ConfidenceLevel
from bot.db.models import MealInputType, User
from bot.handlers.meal import MealStates, OnboardingCompleted, save_and_reply_meal

logger = structlog.get_logger(__name__)
router = Router(name="photo")

# Always shown for photo-based estimates — product honesty requirement
_PHOTO_DISCLAIMER = (
    "Оценка по фото — приблизительная. "
    "Точность зависит от качества снимка и видимости порций."
)

# Wording for different confidence levels shown before the result
_CONFIDENCE_NOTE = {
    ConfidenceLevel.high: None,  # no extra note
    ConfidenceLevel.medium: "Оценка приблизительная — блюдо понятно, но порция может отличаться.",
    ConfidenceLevel.low: "Уверенность низкая — порции трудно определить по фото. Данные очень ориентировочные.",
}


@router.message(
    OnboardingCompleted(),
    StateFilter(None, MealStates.awaiting_correction),
    F.photo,
)
async def handle_photo_meal(
    message: Message,
    user: User,
    bot: Bot,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    await state.clear()

    log = logger.bind(telegram_id=user.telegram_id)

    # Caption (if any) is passed as a hint to the vision model
    user_hint: str | None = message.caption.strip() if message.caption else None

    # ── 1. Download highest-resolution photo ──────────────────────────────────
    # Telegram provides multiple sizes; last element is the largest.
    photo = message.photo[-1]

    try:
        file = await bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        image_bytes = buf.getvalue()
    except Exception as exc:
        log.error("photo_download_failed", error=str(exc))
        await message.answer(
            "Не удалось загрузить фото. Попробуй ещё раз или опиши еду текстом/голосом."
        )
        return

    log.info("photo_downloaded", file_size=len(image_bytes), has_hint=bool(user_hint))

    # ── 2. Analyze via Vision provider ────────────────────────────────────────
    provider = get_vision_provider()
    try:
        result = await provider.analyze_meal_photo(
            image_bytes=image_bytes,
            mime_type="image/jpeg",  # Telegram photos are always JPEG
            user_hint=user_hint,
        )
    except Exception as exc:
        log.error("photo_analysis_failed", error=str(exc))
        await message.answer(
            "Не смог проанализировать фото — попробуй ещё раз или опиши еду текстом/голосом."
        )
        return
    finally:
        # Release image bytes immediately — do not hold in memory
        del image_bytes
        buf.close()

    log.info(
        "photo_analyzed",
        confidence=result.confidence,
        needs_clarification=result.needs_clarification,
        items=len(result.items),
    )

    # ── 3. Handle low-confidence / ambiguous result ───────────────────────────
    if result.needs_clarification:
        clarification = result.clarification_prompt or (
            "Не смог точно определить блюдо по фото. "
            "Опиши, что ел(а), текстом или голосом — и я всё посчитаю."
        )
        if result.confidence_notes:
            clarification = f"{clarification}\n\n<i>Причина: {result.confidence_notes}</i>"
        await message.answer(clarification)
        return

    # ── 4. Build disclaimer for this confidence level ─────────────────────────
    conf_note = _CONFIDENCE_NOTE.get(result.confidence)
    disclaimer_parts = [_PHOTO_DISCLAIMER]
    if conf_note:
        disclaimer_parts.append(conf_note)
    disclaimer = " ".join(disclaimer_parts)

    # ── 5. Save + reply (shared with text/voice flow) ─────────────────────────
    await save_and_reply_meal(
        message=message,
        result=result,
        input_type=MealInputType.photo,
        raw_input=user_hint,  # store caption hint as raw_input; photo itself is not persisted
        user=user,
        db=db,
        disclaimer=disclaimer,
    )


# ── Guard: photo from user who hasn't finished onboarding ─────────────────────

@router.message(StateFilter(None), F.photo)
async def handle_photo_no_profile(message: Message, user: User) -> None:
    if user.onboarding_state.value != "completed":
        await message.answer("Сначала нужно заполнить профиль. Напиши /start.")
    elif not user.targets_set:
        await message.answer(
            "Профиль неполный — зайди в /profile и заполни недостающие поля."
        )
