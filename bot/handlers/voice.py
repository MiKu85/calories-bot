"""
Telegram voice message handler.

Flow:
  voice message
    → download audio bytes from Telegram (in-memory, never persisted)
    → STTProvider.transcribe(bytes) → Russian text
    → empty / too short? → ask to type manually
    → run_meal_pipeline(text, input_type=voice)  ← same as text flow

Error / fallback scenarios:
  1. File download fails       → ask to retry
  2. STT provider error        → ask to retry or type manually
  3. Empty transcription       → ask to type manually
  4. Low-confidence transcription that produces low-confidence meal analysis
                               → AI returns needs_clarification → ask to clarify
                               (handled inside run_meal_pipeline, no special case here)
"""
from __future__ import annotations

import io

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.ai import get_stt_provider
from bot.db.models import MealInputType, User
from bot.handlers.meal import MealStates, OnboardingCompleted, run_meal_pipeline

logger = structlog.get_logger(__name__)
router = Router(name="voice")

_MIN_TRANSCRIPTION_LENGTH = 3  # chars — anything shorter is treated as failed


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
    db: AsyncSession,
) -> None:
    await state.clear()

    log = logger.bind(
        telegram_id=user.telegram_id,
        duration=message.voice.duration,
        file_size=message.voice.file_size,
    )

    # ── 1. Download audio into memory ─────────────────────────────────────────
    try:
        file = await bot.get_file(message.voice.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        audio_bytes = buf.getvalue()
    except Exception as exc:
        log.error("voice_download_failed", error=str(exc))
        await message.answer(
            "Не удалось загрузить голосовое сообщение. Попробуй ещё раз или напиши текстом."
        )
        return

    # ── 2. Transcribe via STT ──────────────────────────────────────────────────
    stt = get_stt_provider()
    try:
        transcription = await stt.transcribe(audio_bytes, mime_type="audio/ogg")
    except Exception as exc:
        log.error("voice_stt_failed", error=str(exc))
        await message.answer(
            "Не смог распознать голосовое. Попробуй ещё раз или опиши еду текстом."
        )
        return
    finally:
        # Explicitly release audio bytes — do not hold them in memory longer than needed
        del audio_bytes
        buf.close()

    text = transcription.text.strip()
    log.info("voice_transcribed", text_preview=text[:80], language=transcription.language)

    # ── 3. Guard: empty / unusably short transcription ────────────────────────
    if len(text) < _MIN_TRANSCRIPTION_LENGTH:
        await message.answer(
            "Не расслышал — голосовое слишком короткое или тихое.\n"
            "Попробуй ещё раз или напиши, что ел(а), текстом."
        )
        return

    # ── 4. Show transcription so user can verify ──────────────────────────────
    await message.answer(f"<i>Распознал: «{text}»</i>")

    # ── 5. Run the same pipeline as text input ────────────────────────────────
    await run_meal_pipeline(
        message=message,
        text=text,
        input_type=MealInputType.voice,
        user=user,
        db=db,
    )
