"""Запрос к LLM с переключением на запасную модель.

24.10.2026 polza.ai два часа отдавала 503 SERVICE_UNAVAILABLE на
gemini-2.5-flash, и бот отвечал всем «не смог обработать запрос» — семья
решила, что сломала бота. Ретраи той же моделью тут не помогают: недоступна
не сеть, а конкретный провайдер модели, поэтому пробуем другую.
"""
from __future__ import annotations

import structlog
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)

logger = structlog.get_logger(__name__)


def models_chain(primary: str, fallback: str | None) -> tuple[str, ...]:
    """Основная модель и запасная, если она задана и отличается."""
    if fallback and fallback != primary:
        return (primary, fallback)
    return (primary,)


async def create_with_fallback(client: AsyncOpenAI, models: tuple[str, ...], **kwargs):
    """chat.completions.create по цепочке моделей.

    Переключаемся только на ошибках самого провайдера (5xx): 4xx — это наш
    кривой запрос, на другой модели он сломается так же.
    """
    last: Exception | None = None
    for position, model in enumerate(models):
        try:
            return await client.chat.completions.create(model=model, **kwargs)
        except APIStatusError as exc:
            if exc.status_code < 500 or position == len(models) - 1:
                raise
            logger.warning(
                "llm_model_unavailable",
                model=model,
                status=exc.status_code,
                next_model=models[position + 1],
            )
            last = exc
    raise last  # сюда не попадаем: последняя модель всегда пробрасывает своё


def is_provider_outage(exc: BaseException) -> bool:
    """Это сбой на стороне LLM, а не кривой запрос и не наша логика.

    Нужно хендлерам: при недоступности провайдера человеку говорят «попробуй
    позже», а не «не смог обработать» — иначе он думает, что сломал бота.
    """
    if isinstance(exc, APIStatusError):
        return exc.status_code >= 500
    return isinstance(exc, (APIConnectionError, APITimeoutError))
