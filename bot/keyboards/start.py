from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings


def subscription_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Подписаться на канал",
        url=f"https://t.me/{settings.telegram_channel_id.lstrip('@')}",
    )
    builder.button(text="Я подписался — проверить", callback_data="check_subscription")
    builder.adjust(1)
    return builder.as_markup()


def welcome_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Начать", callback_data="start_onboarding")
    return builder.as_markup()
