"""
Voice message handler — routes through the debounce buffer (Task 5).

Flow:
  voice message
    → download audio bytes from Telegram (in-memory, never persisted)
    → kick off STT transcription immediately (parallel to buffering!)
    → add BufferedMessage (with the running stt_task) to MealDebounceService
    → debounce service fires flush_meal_buffer() after silence window
    → flush awaits stt_task, assembles BatchMessage, calls LLM, saves, replies

Starting STT before the timer fires is intentional: Whisper takes 1-5 s, so
by the time the 12-s silence window expires the transcript is usually ready and
the final LLM call doesn't have to wait for it.

Error / fallback scenarios:
  1. File download fails       → ask to retry
  2. STT task fails            → flush skips this message with a warning log
  3. Empty transcription       → flush skips this message (too short)
"""
from __future__ import annotations

import asyncio
import io

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.ai.factory import get_stt_provider
from bot.db.models import User
from bot.handlers.meal import MealStates, OnboardingCompleted
from bot.services.debounce_service import BufferedMessage, meal_debounce_service

logger = structlog.get_logger(__name__)
router = Router(name="voice")


async def _run_stt(audio_bytes: bytes, msg: BufferedMessage) -> None:
    """Coroutine stored as an asyncio.Task inside BufferedMessage.stt_task."""
    stt = get_stt_provider()
    try:
        result = await stt.transcribe(audio_bytes, mime_type="audio/ogg")
        msg.stt_result = result.text.strip()
    except Exception as exc:
        logger.warning("background_stt_failed", error=str(exc))
        msg.stt_result = None
    finally:
        del audio_bytes  # release memory as soon as STT is done


@router.message(
    OnboardingCompleted(),
    StateFilter(None, MealStates.awaiting_correction),
    F.voice,
)
async def handle_voice_meal(
    message: Message,
    user: User,
    bot: Bot,
    state: FSMContext,
) -> None:
    await state.clear()

    log = logger.bind(
        telegram_id=user.telegram_id,
        duration=message.voice.duration,
        file_size=message.voice.file_size,
    )

    # ── 1. Download audio ──────────────────────────────────────────────────────
    try:
        file = await bot.get_file(message.voice.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        audio_bytes = buf.getvalue()
        buf.close()
    except Exception as exc:
        log.error("voice_download_failed", error=str(exc))
        await message.answer(
            "Не удалось загрузить голосовое сообщение. Попробуй ещё раз или напиши текстом."
        )
        return

    log.info("voice_downloaded_to_buffer", file_size=len(audio_bytes))

    # ── 2. Create BufferedMessage and kick off STT immediately ─────────────────
    # The STT task runs concurrently with the debounce timer.  By the time the
    # silence window fires (≥12 s), Whisper has almost certainly finished.
    msg = BufferedMessage(
        kind="voice",
        audio_bytes=audio_bytes,
        audio_mime="audio/ogg",
    )
    msg.stt_task = asyncio.create_task(
        _run_stt(audio_bytes, msg),
        name=f"stt_{user.telegram_id}_{message.message_id}",
    )

    # ── 3. Hand off to debounce service ───────────────────────────────────────
    await meal_debounce_service.add_message(
        telegram_id=user.telegram_id,
        chat_id=message.chat.id,
        msg=msg,
    )
