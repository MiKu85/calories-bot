from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def feedback_options_kb() -> InlineKeyboardMarkup:
    """Quick-pick options sent with the feedback request."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Всё нравится", callback_data="fb:like")
    builder.button(text="Иногда неточно считает", callback_data="fb:inaccurate")
    builder.button(text="Неудобно пользоваться", callback_data="fb:inconvenient")
    builder.button(text="Написать комментарий", callback_data="fb:comment")
    builder.adjust(2, 2)
    return builder.as_markup()


def feedback_skip_voice_kb() -> InlineKeyboardMarkup:
    """Shown after text feedback to let user skip the voice step."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Пропустить", callback_data="fb:skip_voice")
    return builder.as_markup()
