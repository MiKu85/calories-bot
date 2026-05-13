"""
Food photo analysis handler — routes through the debounce buffer (Task 5).

Flow:
  photo message (optionally with caption as user hint)
    → download highest-resolution Telegram photo (in-memory, never persisted)
    → add to MealDebounceService buffer
    → MealDebounceService fires flush_meal_buffer() after silence window
    → flush builds BatchMessage, calls LLM, saves meal, replies to user

The actual analysis + save + reply now lives in bot/handlers/meal_batch.py.
This handler is responsible only for downloading the file and handing it off
to the debounce service so the timer starts as early as possible.

Error / fallback scenarios:
  1. Photo download fails → ask to try again or describe by text
  2. Onboarding not complete → redirect to /start
"""
from __future__ import annotations

import io

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.db.models import User
from bot.handlers.meal import MealStates, OnboardingCompleted
from bot.services.debounce_service import BufferedMessage, meal_debounce_service

logger = structlog.get_logger(__name__)
router = Router(name="photo")


@router.message(
    OnboardingCompleted(),
    StateFilter(None, MealStates.awaiting_correction, MealStates.awaiting_patch),
    F.photo,
)
async def handle_photo_meal(
    message: Message,
    user: User,
    bot: Bot,
    state: FSMContext,
) -> None:
    await state.clear()

    log = logger.bind(telegram_id=user.telegram_id)
    caption: str | None = message.caption.strip() if message.caption else None

    # ── Download highest-resolution photo ─────────────────────────────────────
    photo = message.photo[-1]
    try:
        file = await bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        image_bytes = buf.getvalue()
        buf.close()
    except Exception as exc:
        log.error("photo_download_failed", error=str(exc))
        await message.answer(
            "Не удалось загрузить фото. Попробуй ещё раз или опиши еду текстом/голосом."
        )
        return

    log.info("photo_downloaded_to_buffer", file_size=len(image_bytes), has_caption=bool(caption))

    # Save file_id so /retry can re-download this photo later
    await state.update_data(last_photo_file_id=photo.file_id, last_photo_caption=caption)

    # ── Hand off to debounce service ──────────────────────────────────────────
    # The service starts the typing indicator and the silence timer.
    # When the timer fires, flush_meal_buffer() handles the rest.
    msg = BufferedMessage(
        kind="photo",
        image_bytes=image_bytes,
        mime_type="image/jpeg",
        caption=caption,
    )
    await meal_debounce_service.add_message(
        telegram_id=user.telegram_id,
        chat_id=message.chat.id,
        msg=msg,
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
