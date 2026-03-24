"""
Structured output schemas for AI integrations.

All providers must return these models — they are the contract between
AI layer and business logic. Never let raw provider responses leak upward.
"""
from __future__ import annotations

import enum

from pydantic import BaseModel, Field, model_validator


class ConfidenceLevel(str, enum.Enum):
    high = "high"
    medium = "medium"
    low = "low"


class MealItem(BaseModel):
    """Single food item within a meal."""

    name: str = Field(description="Food item name in Russian")
    portion_description: str = Field(description="Portion description, e.g. '200г', '1 тарелка'")
    calories: float = Field(ge=0)
    protein_g: float = Field(ge=0)
    fat_g: float = Field(ge=0)
    carbs_g: float = Field(ge=0)


class MealAnalysisResult(BaseModel):
    """
    Result of meal analysis from any input type (text, voice transcription, photo).

    When needs_clarification=True, the bot must NOT save the meal and must
    ask the user to clarify. It must NOT present uncertain numbers as facts.
    """

    items: list[MealItem] = Field(default_factory=list)

    total_calories: float = Field(ge=0)
    total_protein_g: float = Field(ge=0)
    total_fat_g: float = Field(ge=0)
    total_carbs_g: float = Field(ge=0)

    confidence: ConfidenceLevel
    confidence_notes: str | None = Field(
        default=None,
        description="What made the model uncertain — shown to user when confidence is low",
    )

    needs_clarification: bool = Field(
        default=False,
        description="True when confidence is too low to save; bot must ask user to clarify",
    )
    clarification_prompt: str | None = Field(
        default=None,
        description="Russian question to ask the user when needs_clarification=True",
    )

    @model_validator(mode="after")
    def clarification_requires_prompt(self) -> MealAnalysisResult:
        if self.needs_clarification and not self.clarification_prompt:
            self.clarification_prompt = (
                "Не могу точно определить блюдо. Опиши подробнее — что именно ел(а) и примерный объём?"
            )
        return self


class TranscriptionResult(BaseModel):
    """Result of speech-to-text transcription."""

    text: str
    language: str = "ru"
