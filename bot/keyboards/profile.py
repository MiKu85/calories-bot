from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def profile_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Изменить вес", callback_data="profile_edit:weight")
    builder.button(text="Изменить активность", callback_data="profile_edit:activity")
    builder.button(text="Изменить цель", callback_data="profile_edit:goal")
    builder.button(text="Пересчитать цели", callback_data="profile_edit:recalc")
    builder.button(text="Сбросить профиль", callback_data="profile_edit:reset")
    builder.adjust(2, 2, 1)
    return builder.as_markup()
