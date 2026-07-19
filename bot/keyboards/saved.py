from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import SavedMeal


def saved_meals_list_kb(saved: list[SavedMeal]) -> InlineKeyboardMarkup:
    """Список сохранённых блюд: тап по блюду — добавить в дневник."""
    builder = InlineKeyboardBuilder()
    for s in saved:
        builder.button(text=f"🍽 {s.name}", callback_data=f"saved_add:{s.id}")
    builder.button(text="⚙️ Управлять", callback_data="saved_manage")
    builder.adjust(1)
    return builder.as_markup()


def saved_meals_manage_kb(saved: list[SavedMeal]) -> InlineKeyboardMarkup:
    """Управление: тап по блюду — переименовать/удалить."""
    builder = InlineKeyboardBuilder()
    for s in saved:
        builder.button(text=f"✏️ {s.name}", callback_data=f"saved_edit:{s.id}")
    builder.button(text="⬅️ Назад", callback_data="saved_list")
    builder.adjust(1)
    return builder.as_markup()


def saved_meal_actions_kb(saved_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Переименовать", callback_data=f"saved_rename:{saved_id}")
    builder.button(text="🗑 Удалить", callback_data=f"saved_delete:{saved_id}")
    builder.button(text="⬅️ Назад", callback_data="saved_manage")
    builder.adjust(2, 1)
    return builder.as_markup()
