from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


def sex_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Мужской", callback_data="sex:male")
    builder.button(text="Женский", callback_data="sex:female")
    builder.adjust(2)
    return builder.as_markup()


def activity_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Сидячий (без спорта)", callback_data="activity:sedentary")
    builder.button(text="Лёгкая (1-2 раза/нед)", callback_data="activity:light")
    builder.button(text="Умеренная (3-4 раза/нед)", callback_data="activity:moderate")
    builder.button(text="Высокая (5+ раз/нед)", callback_data="activity:active")
    builder.button(text="Очень высокая (спортсмен)", callback_data="activity:very_active")
    builder.adjust(1)
    return builder.as_markup()


def goal_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Похудеть", callback_data="goal:lose")
    builder.button(text="Поддерживать вес", callback_data="goal:maintain")
    builder.button(text="Набрать массу", callback_data="goal:gain")
    builder.adjust(1)
    return builder.as_markup()


def workouts_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for n in range(0, 7):
        builder.button(text=str(n), callback_data=f"workouts:{n}")
    builder.button(text="7+", callback_data="workouts:7")
    builder.button(text="Пропустить", callback_data="workouts:skip")
    builder.adjust(4, 4, 1)
    return builder.as_markup()


def remove_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
