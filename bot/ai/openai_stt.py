"""
OpenAI Whisper STT provider.

Receives raw audio bytes (Telegram OGG/OPUS) and returns a Russian transcription.
The transcription is then passed through the same text meal analysis pipeline.
"""
from __future__ import annotations

import io

import structlog
from openai import AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from bot.ai.schemas import TranscriptionResult

logger = structlog.get_logger(__name__)

# Telegram voice messages arrive as OGG/OPUS; Whisper supports this natively.
_FILENAME_BY_MIME = {
    "audio/ogg": "voice.ogg",
    "audio/mpeg": "voice.mp3",
    "audio/mp4": "voice.mp4",
    "audio/wav": "voice.wav",
    "audio/webm": "voice.webm",
}


class OpenAISTTProvider:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/ogg",
    ) -> TranscriptionResult:
        log = logger.bind(model=self._model, audio_size=len(audio_bytes), mime_type=mime_type)

        if not audio_bytes:
            log.warning("stt_empty_audio")
            return TranscriptionResult(text="", language="ru")

        filename = _FILENAME_BY_MIME.get(mime_type, "voice.ogg")
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = filename

        try:
            response = await self._client.audio.transcriptions.create(
                model=self._model,
                file=audio_file,
                language="ru",
                response_format="verbose_json",
            )

            text = (response.text or "").strip()
            language = getattr(response, "language", "ru") or "ru"
            log.info("stt_transcribed", text_preview=text[:80], language=language)
            return TranscriptionResult(text=text, language=language)

        except Exception as exc:
            log.error("stt_provider_error", error=str(exc))
            raise
