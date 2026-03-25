"""
OpenAI text meal analysis provider.

Uses structured JSON output (response_format) so the response is always
a valid JSON object — no fragile regex parsing.
"""
from __future__ import annotations

import json

import structlog
from openai import AsyncOpenAI
from pydantic import ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from bot.ai.schemas import ConfidenceLevel, MealAnalysisResult, MealItem

logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """\
Ты — помощник-нутрициолог. Тебе дают описание еды на русском языке.
Твоя задача — оценить КБЖУ (калории, белки, жиры, углеводы).

Правила:
1. Всегда возвращай валидный JSON строго по схеме.
2. Раздели блюдо на отдельные компоненты (items), если их несколько.
3. НИКОГДА не спрашивай вес в граммах. Люди не взвешивают еду — они называют блюдо и примерный размер.
4. Используй стандартные справочные веса для оценки порций:
   - средний банан ~120г, маленький ~80г, большой ~160г
   - средний киви ~80г, среднее яблоко ~180г, средний апельсин ~160г
   - средний сырник ~55г, средняя котлета ~80г, средняя куриная грудка ~150г
   - тарелка супа/каши ~300г, порция гарнира ~150-200г, ломтик хлеба ~30г
   - стакан молока/сока ~200мл, чашка кофе ~150мл
   - если написано "большой/крупный" — увеличь на 30-40%, "маленький/мелкий" — уменьши на 30-40%
5. Если порция вообще не указана и непонятна по контексту — предположи стандартную и выставь confidence="medium".
6. needs_clarification=true ТОЛЬКО если совершенно непонятно КАКОЙ продукт имеется в виду.
   Если непонятен только размер — спроси "большая, средняя или маленькая порция?" (но не вес в граммах).
7. confidence="high" если блюдо и размер понятны. confidence="medium" если размер приблизительный.
8. Напитки включай только если явно упомянуты.
9. Все текстовые поля — на русском языке.
"""

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
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
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
            data = json.loads(raw)
            result = MealAnalysisResult.model_validate(data)
            log.info("meal_text_analyzed", confidence=result.confidence, items=len(result.items))
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
