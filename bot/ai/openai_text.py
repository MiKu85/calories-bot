"""
OpenAI text meal analysis provider.

Uses structured JSON output (response_format) so the response is always
a valid JSON object — no fragile regex parsing.

System prompt is loaded from bot/prompts/parse_meal.txt so it can be
edited without a code redeploy.
"""
from __future__ import annotations

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

_PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "parse_meal.txt"


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


class OpenAITextProvider:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def analyze_meal(self, text: str) -> MealAnalysisResult:
        log = logger.bind(model=self._model, input_preview=text[:80])
        system_prompt = _load_system_prompt()
        log.debug("meal_text_prompt", prompt_chars=len(system_prompt), user_input=text)
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
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
                max_tokens=1024,
            )

            raw = response.choices[0].message.content
            log.debug("meal_text_raw_response", raw=raw)
            data = json.loads(raw)
            result = MealAnalysisResult.model_validate(data)
            log.info("meal_text_analyzed", confidence=result.confidence, items=len(result.items))
            log.debug("meal_text_parsed", items=data.get("items"))
            return result

        except (json.JSONDecodeError, ValidationError) as exc:
            log.error("meal_text_parse_error", error=str(exc))
            return _fallback_clarification()
        except Exception as exc:
            log.error("meal_text_provider_error", error=str(exc))
            raise


def _fallback_clarification() -> MealAnalysisResult:
    """Returned when the model output cannot be parsed — ask user to retry."""
    return MealAnalysisResult(
        items=[],
        total_calories=0,
        total_protein_g=0,
        total_fat_g=0,
        total_carbs_g=0,
        confidence=ConfidenceLevel.low,
        confidence_notes="Не удалось разобрать ответ модели",
        needs_clarification=True,
        clarification_prompt="Что-то пошло не так. Опиши приём пищи ещё раз, пожалуйста.",
    )
