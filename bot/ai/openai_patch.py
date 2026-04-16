"""
OpenAI meal patch provider.

Receives the current meal items (with temporary ids) and the user's
free-text correction, returns an updated items list.

System prompt is loaded from bot/prompts/patch_meal.txt.
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

from bot.ai.schemas import ExtraMeal, ExtraMealItem, MealPatchResult, PatchedItem

logger = structlog.get_logger(__name__)

_PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "patch_meal.txt"


def _load_system_prompt() -> str:
    return _PROMPT_FILE.read_text(encoding="utf-8").strip()


_PATCHED_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "portion_description": {"type": "string"},
        "calories": {"type": "number"},
        "protein_g": {"type": "number"},
        "fat_g": {"type": "number"},
        "carbs_g": {"type": "number"},
    },
    "required": ["id", "name", "portion_description", "calories", "protein_g", "fat_g", "carbs_g"],
    "additionalProperties": False,
}

_EXTRA_ITEM_SCHEMA = {
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

_EXTRA_MEAL_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": _EXTRA_ITEM_SCHEMA},
        "total_calories": {"type": "number"},
        "total_protein_g": {"type": "number"},
        "total_fat_g": {"type": "number"},
        "total_carbs_g": {"type": "number"},
    },
    "required": ["items", "total_calories", "total_protein_g", "total_fat_g", "total_carbs_g"],
    "additionalProperties": False,
}

_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "understood": {"type": "boolean"},
        "clarification_prompt": {"type": ["string", "null"]},
        "items": {"type": "array", "items": _PATCHED_ITEM_SCHEMA},
        "total_calories": {"type": "number"},
        "total_protein_g": {"type": "number"},
        "total_fat_g": {"type": "number"},
        "total_carbs_g": {"type": "number"},
        "extra_meals": {"type": "array", "items": _EXTRA_MEAL_SCHEMA},
    },
    "required": [
        "understood", "clarification_prompt",
        "items", "total_calories", "total_protein_g", "total_fat_g", "total_carbs_g",
        "extra_meals",
    ],
    "additionalProperties": False,
}


class OpenAIPatchProvider:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def patch_meal(
        self,
        current_items: list[dict],
        user_message: str,
    ) -> MealPatchResult:
        """
        Apply a natural-language correction to the current meal items.

        current_items: list of dicts with keys id, name, portion_description,
                       calories, protein_g, fat_g, carbs_g.
        user_message: the correction in plain Russian.
        """
        log = logger.bind(model=self._model, correction_preview=user_message[:80])
        system_prompt = _load_system_prompt()

        user_content = (
            f"Текущие позиции приёма:\n{json.dumps(current_items, ensure_ascii=False, indent=2)}\n\n"
            f"Сообщение пользователя: {user_message}"
        )
        log.debug("meal_patch_prompt", prompt_chars=len(system_prompt), user_content=user_content)

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
                        "name": "meal_patch",
                        "strict": True,
                        "schema": _JSON_SCHEMA,
                    },
                },
                temperature=0.1,
                max_tokens=1024,
            )

            raw = response.choices[0].message.content
            log.debug("meal_patch_raw_response", raw=raw)
            data = json.loads(raw)
            result = MealPatchResult.model_validate(data)
            log.info(
                "meal_patched",
                understood=result.understood,
                items=len(result.items),
            )
            return result

        except (json.JSONDecodeError, ValidationError) as exc:
            log.error("meal_patch_parse_error", error=str(exc))
            return _fallback_not_understood()
        except Exception as exc:
            log.error("meal_patch_provider_error", error=str(exc))
            raise


def _fallback_not_understood() -> MealPatchResult:
    return MealPatchResult(
        understood=False,
        clarification_prompt="Не смог применить правку. Попробуй написать иначе — например «сырники не 220, а 150г».",
        items=[],
        total_calories=0,
        total_protein_g=0,
        total_fat_g=0,
        total_carbs_g=0,
    )


def items_to_patch_input(meal_items: list[dict]) -> list[dict]:
    """
    Convert stored meal_items (no ids) to the format expected by the patch provider
    (with sequential int ids).
    """
    return [
        {
            "id": i,
            "name": item.get("name", ""),
            "portion_description": item.get("portion_description", ""),
            "calories": item.get("calories", 0),
            "protein_g": item.get("protein_g", 0),
            "fat_g": item.get("fat_g", 0),
            "carbs_g": item.get("carbs_g", 0),
        }
        for i, item in enumerate(meal_items)
    ]


def patch_result_to_items(patched_items: list[PatchedItem]) -> list[dict]:
    """Convert patch result items back to the DB-stored format (no ids)."""
    return [
        {
            "name": item.name,
            "portion_description": item.portion_description,
            "calories": item.calories,
            "protein_g": item.protein_g,
            "fat_g": item.fat_g,
            "carbs_g": item.carbs_g,
        }
        for item in patched_items
    ]
