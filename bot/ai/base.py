"""
Abstract provider interfaces.

Business logic depends only on these Protocols — never on a concrete SDK.
Swapping a provider means writing a new class that satisfies the Protocol.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from bot.ai.schemas import MealAnalysisResult, TranscriptionResult


@runtime_checkable
class TextProvider(Protocol):
    """Analyzes a text or voice-transcription meal description."""

    async def analyze_meal(self, text: str) -> MealAnalysisResult:
        """
        Parse a Russian meal description and return structured KBJU estimate.

        Must never raise on partial/ambiguous input — return low confidence instead.
        """
        ...


@runtime_checkable
class VisionProvider(Protocol):
    """Analyzes a food photo."""

    async def analyze_meal_photo(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        user_hint: str | None = None,
    ) -> MealAnalysisResult:
        """
        Estimate KBJU from a food photo.

        - If multiple dishes are visible, split into separate MealItems.
        - If confidence is low, set needs_clarification=True and provide
          a clarification_prompt — do NOT fabricate numbers.
        - Drinks are included only when clearly visible.

        user_hint: optional text from the user describing the photo.
        """
        ...


@runtime_checkable
class STTProvider(Protocol):
    """Transcribes Telegram voice messages to text."""

    async def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/ogg",
    ) -> TranscriptionResult:
        """
        Transcribe voice audio to Russian text.

        Must never raise on short/silent audio — return empty text instead.
        """
        ...
