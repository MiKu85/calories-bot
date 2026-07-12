"""
OpenAI vision meal analysis provider.

Images are passed as base64 inline data — not stored after analysis.
Low-confidence results always trigger a clarification request rather
than fabricated numbers.

System prompt is loaded from bot/prompts/vision_meal.txt so it can be
edited without a code redeploy.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

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

_PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "vision_meal.txt"


def _load_system_prompt() -> str:
    return _PROMPT_FILE.read_text(encoding="utf-8").strip()


_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
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
            },
        },
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


class OpenAIVisionProvider:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def analyze_meal_photo(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        user_hint: str | None = None,
    ) -> MealAnalysisResult:
        log = logger.bind(model=self._model, image_size=len(image_bytes), has_hint=bool(user_hint))
        system_prompt = _load_system_prompt()
        log.debug("meal_vision_prompt", prompt_chars=len(system_prompt), user_hint=user_hint)

        b64 = base64.b64encode(image_bytes).decode()
        image_url = f"data:{mime_type};base64,{b64}"

        user_content: list[dict] = [
            {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
        ]
        if user_hint:
            user_content.append({"type": "text", "text": f"Подсказка от пользователя: {user_hint}"})

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "meal_analysis",
                        "strict": True,
                        "schema": _JSON_SCHEMA,
                    },
                },
                temperature=0.2,
                max_tokens=2048,  # большие приёмы (много позиций) не должны обрезаться
            )

            raw = response.choices[0].message.content
            log.debug("meal_vision_raw_response", raw=raw)
            data = json.loads(raw)
            result = MealAnalysisResult.model_validate(data)
            log.info(
                "meal_photo_analyzed",
                confidence=result.confidence,
                needs_clarification=result.needs_clarification,
                items=len(result.items),
            )
            log.debug("meal_vision_parsed", items=data.get("items"))
            return result

        except (json.JSONDecodeError, ValidationError) as exc:
            log.error("meal_photo_parse_error", error=str(exc))
            return _fallback_clarification()
        except Exception as exc:
            log.error("meal_photo_provider_error", error=str(exc))
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
        clarification_prompt="Не смог разобрать фото. Опиши, что ел(а), текстом или голосом.",
    )
