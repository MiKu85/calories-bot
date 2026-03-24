"""
/help — справка по командам и использованию бота.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="help")

_HELP_TEXT = """<b>Как пользоваться ботом</b>

Просто напиши, что ел(а), и я оценю КБЖУ:
· текстом: «Съел тарелку борща и хлеб»
· голосом: запиши голосовое сообщение
· фото: пришли снимок тарелки

После каждого приёма я покажу остаток на день.

<b>Команды</b>
/stats — статистика за сегодня
/profile — мой профиль и цели на день
/reset — сбросить профиль или начать заново
/help — эта справка

<b>Кнопки после приёма пищи</b>
✅ Верно — подтвердить и сохранить
✏️ Исправить — описать заново (текстом или голосом)
📊 Статистика за сегодня — открыть /stats

<b>Важно</b>
Оценки по фото и голосу приблизительны.
Бот не заменяет консультацию диетолога или врача."""


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(_HELP_TEXT)
