"""
OpenAI batch meal analysis provider — used by the debounce flush pipeline.

Accepts a mixed sequence of messages (photos + voice transcripts + plain text)
collected during one debounce window and returns one *or more* MealAnalysisResult
objects. The model decides whether to merge them into a single meal or split
them into several based on semantic content — code does not second-guess it.

Prompt is loaded from bot/prompts/parse_meal_batch.txt.
Response format: {"meals": [<meal_object>, ...]}
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog
from openai import AsyncOpenAI
from pydantic import ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from bot.ai.schemas import ConfidenceLevel, MealAnalysisResult

logger = structlog.get_logger(__name__)

_PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "parse_meal_batch.txt"


def _load_system_prompt() -> str:
    return _PROMPT_FILE.read_text(encoding="utf-8").strip()


@dataclass
class BatchMessage:
    """A single message from the debounce buffer, ready for LLM analysis."""

    kind: Literal["photo", "voice_transcript", "text"]
    delay_seconds: float  # seconds elapsed since the first message in the batch

    # Photo
    image_bytes: bytes | None = None
    mime_type: str = "image/jpeg"
    caption: str | None = None  # user's text hint alongside the photo

    # Text or voice transcript
    text: str | None = None


# ── JSON schema ───────────────────────────────────────────────────────────────

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "portion_description": {"type": "string"},
        "calories": {"type": "number"},
        "protein_g": {"type": "number"},
        "fat_g": {"type": "number"},
        "carbs_g": {"type": "number"},
    },
    "required": ["name", "portion_description", "calories", "protein_g", "fat_g", "carbs_g"],
    "additionalProperties": False,
}

_MEAL_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": _ITEM_SCHEMA},
        "total_calories": {"type": "number"},
        "total_protein_g": {"type": "number"},
        "total_fat_g": {"type": "number"},
        "total_carbs_g": {"type": "number"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "confidence_notes": {"type": ["string", "null"]},
        "needs_clarification": {"type": "boolean"},
        "clarification_prompt": {"type": ["string", "null"]},
    },
    "required": [
        "items", "total_calories", "total_protein_g", "total_fat_g", "total_carbs_g",
        "confidence", "confidence_notes", "needs_clarification", "clarification_prompt",
    ],
    "additionalProperties": False,
}

_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "meals": {"type": "array", "items": _MEAL_SCHEMA},
    },
    "required": ["meals"],
    "additionalProperties": False,
}


# ── Provider ──────────────────────────────────────────────────────────────────

class OpenAIBatchProvider:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def analyze_meal_batch(
        self, messages: list[BatchMessage]
    ) -> list[MealAnalysisResult]:
        """
        Analyze a mixed batch of messages and return one or more meal objects.

        Returns a list with one element in the typical case (photo + clarification).
        Returns multiple elements only when the model detects clearly separate meals.
        """
        log = logger.bind(model=self._model, msg_count=len(messages))
        system_prompt = _load_system_prompt()

        # ── Build the user message ─────────────────────────────────────────────
        # Text part: describe the batch structure so the model knows the order.
        total_seconds = messages[-1].delay_seconds if messages else 0.0
        lines = [
            f"Пользователь прислал {len(messages)} сообщения(й) за {total_seconds:.0f} секунд.\n"
            "Сообщения (в порядке отправки):\n"
        ]
        for i, msg in enumerate(messages, 1):
            delay_str = (
                "сразу" if msg.delay_seconds < 1 else f"через {msg.delay_seconds:.0f} сек"
            )
            if msg.kind == "photo":
                if msg.caption:
                    lines.append(f"{i}. [ФОТО с подписью пользователя, {delay_str}]: «{msg.caption}»")
                else:
                    lines.append(f"{i}. [ФОТО] ({delay_str})")
            elif msg.kind == "voice_transcript":
                lines.append(f"{i}. [ГОЛОС, транскрипт, {delay_str}]: «{msg.text}»")
            else:
                lines.append(f"{i}. [ТЕКСТ, {delay_str}]: «{msg.text}»")

        user_content: list[dict] = [{"type": "text", "text": "\n".join(lines)}]

        # Inline all photos after the text description.
        # The model sees both the numbered list and the actual images in order.
        for msg in messages:
            if msg.kind == "photo" and msg.image_bytes:
                b64 = base64.b64encode(msg.image_bytes).decode()
                data_url = f"data:{msg.mime_type};base64,{b64}"
                user_content.append(
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}}
                )

        log.debug("batch_prompt_built", text_parts=len(lines), images=sum(
            1 for m in messages if m.kind == "photo"
        ))

        _response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "meal_batch_analysis",
                "strict": True,
                "schema": _BATCH_SCHEMA,
            },
        }

        raw: str | None = None
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                response_format=_response_format,
                temperature=0.2,
                max_tokens=2048,
            )

            raw = response.choices[0].message.content
            log.debug("batch_raw_response", preview=raw[:300])
            data = json.loads(raw)
            results = [MealAnalysisResult.model_validate(m) for m in data.get("meals", [])]

            if not results:
                results = [_fallback_clarification()]

            log.info(
                "batch_analyzed",
                meal_count=len(results),
                multi_meal=len(results) > 1,
            )
            return results

        except (json.JSONDecodeError, ValidationError) as exc:
            log.error(
                "batch_parse_error",
                error=str(exc),
                raw_preview=raw[:500] if raw else None,
            )
            # Automatic retry — ask model to return strict JSON
            retry_raw: str | None = None
            try:
                retry_messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ]
                if raw is not None:
                    retry_messages.append({"role": "assistant", "content": raw})
                retry_messages.append({
                    "role": "user",
                    "content": "Верни ответ строго в формате JSON. Никакого дополнительного текста.",
                })
                retry_response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=retry_messages,
                    response_format=_response_format,
                    temperature=0.1,
                    max_tokens=2048,
                )
                retry_raw = retry_response.choices[0].message.content
                log.info("batch_parse_retry_success", preview=retry_raw[:300])
                data = json.loads(retry_raw)
                results = [MealAnalysisResult.model_validate(m) for m in data.get("meals", [])]
                if results:
                    return results
            except Exception as retry_exc:
                log.error(
                    "batch_parse_retry_failed",
                    error=str(retry_exc),
                    retry_raw_preview=retry_raw[:500] if retry_raw else None,
                )
            return [_fallback_clarification()]

        except Exception as exc:
            log.error("batch_provider_error", error=str(exc))
            raise


def _fallback_clarification() -> MealAnalysisResult:
    return MealAnalysisResult(
        items=[],
        total_calories=0,
        total_protein_g=0,
        total_fat_g=0,
        total_carbs_g=0,
        confidence=ConfidenceLevel.low,
        confidence_notes="Не удалось разобрать ответ модели",
        needs_clarification=True,
        clarification_prompt="Не смог обработать — попробуй описать приём заново.",
    )
