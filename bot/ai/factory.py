"""
AI provider factory.

Returns provider instances based on environment config.
All providers are singletons created once at startup and reused.

Adding a new provider (e.g. Anthropic for text):
1. Create bot/ai/anthropic_text.py implementing TextProvider Protocol
2. Add a branch in _build_text_provider() below
3. Set TEXT_MODEL_PROVIDER=anthropic in .env
"""
from __future__ import annotations

from functools import lru_cache

from openai import AsyncOpenAI

from bot.ai.base import STTProvider, TextProvider, VisionProvider
from bot.ai.openai_stt import OpenAISTTProvider
from bot.ai.openai_text import OpenAITextProvider
from bot.ai.openai_vision import OpenAIVisionProvider
from config import settings


@lru_cache(maxsize=1)
def _openai_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)


def get_text_provider() -> TextProvider:
    provider = settings.text_model_provider.lower()
    if provider == "openai":
        return OpenAITextProvider(
            client=_openai_client(),
            model=settings.text_model_name,
        )
    raise ValueError(f"Unknown text model provider: {provider!r}. Supported: openai")


def get_vision_provider() -> VisionProvider:
    provider = settings.vision_model_provider.lower()
    if provider == "openai":
        return OpenAIVisionProvider(
            client=_openai_client(),
            model=settings.vision_model_name,
        )
    raise ValueError(f"Unknown vision model provider: {provider!r}. Supported: openai")


def get_stt_provider() -> STTProvider:
    provider = settings.stt_provider.lower()
    if provider == "openai":
        return OpenAISTTProvider(
            client=_openai_client(),
            model=settings.stt_model_name,
        )
    raise ValueError(f"Unknown STT provider: {provider!r}. Supported: openai")
