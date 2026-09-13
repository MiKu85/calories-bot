"""
Ответ на сообщения, которые не подхватил ни один хендлер.

Без него бот в незакрытом FSM-состоянии молча проглатывает всё, что не подходит
под ожидаемый тип ответа (так пользователь однажды застрял в вопросе про
название блюда: фото уходили в пустоту). Роутер подключается последним, поэтому
сюда попадает только то, что действительно осталось без обработки.
"""
from __future__ import annotations

import structlog
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.db.models import User

logger = structlog.get_logger(__name__)
router = Router(name="fallback")

_ONBOARDING_HINT = (
    "Сначала закончим профиль — ответь на вопрос выше.\n\n"
    "/start — начать заново."
)
_BUSY_HINT = (
    "Жду ответ на предыдущий вопрос.\n\n"
    "/cancel — выйти и вернуться к обычному режиму."
)
_IDLE_HINT = (
    "Не понял сообщение. Пришли фото еды, опиши текстом или запиши голосовое.\n\n"
    "/help — справка."
)


@router.message()
async def handle_unmatched(message: Message, state: FSMContext, user: User | None = None) -> None:
    current = await state.get_state()
    logger.bind(telegram_id=getattr(user, "telegram_id", None)).info(
        "unhandled_message", state=current, content_type=message.content_type,
    )
    if current and current.startswith("OnboardingStates"):
        await message.answer(_ONBOARDING_HINT)
    elif current:
        await message.answer(_BUSY_HINT)
    else:
        await message.answer(_IDLE_HINT)
