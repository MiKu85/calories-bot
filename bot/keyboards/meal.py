from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def meal_result_kb(meal_id: int) -> InlineKeyboardMarkup:
    """Keyboard shown after meal is saved (pending user confirmation)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Верно", callback_data=f"meal_ok:{meal_id}")
    builder.button(text="✏️ Исправить", callback_data=f"meal_fix:{meal_id}")
    builder.button(text="📊 Статистика за сегодня", callback_data="meal_stats")
    builder.adjust(2, 1)
    return builder.as_markup()
