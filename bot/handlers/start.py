"""
/start handler and subscription gate.

Flow:
  /start
    → check subscription
      → not subscribed: subscription_kb (url + re-check button)
      → subscribed:
          → onboarding completed: show main hint
          → onboarding in progress: resume
          → onboarding new: show welcome + disclaimer + [Начать]
"""
from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import OnboardingState, User
from bot.keyboards.start import subscription_kb, welcome_kb
from bot.services.subscription import is_subscribed
from config import settings

router = Router(name="start")

_NOT_SUBSCRIBED = (
    "Для доступа к боту нужно подписаться на канал {channel}.\n\n"
    "После подписки нажми кнопку ниже."
)

_WELCOME = (
    "Привет!\n\n"
    "Я помогу отслеживать приемы пищи, КБЖУ и постепенно выстраивать устойчивые привычки "
    "в питании — без жесткого контроля и чувства вины.\n\n"
    "Здесь не нужно быть идеальными.\n"
    "Можно спокойно двигаться в своем темпе к своим целям.\n\n"
    "А теперь давайте узнаем ваши цели. Ответьте, пожалуйста, на несколько вопросов."
)

_ALREADY_DONE = (
    "Привет снова! Напиши или пришли фото того, что поел(а), и я всё посчитаю.\n\n"
    "Команды: /stats — статистика, /profile — профиль, /help — помощь."
)

_RESUME = "Продолжим с того места, где остановились."


@router.message(Command("start"))
async def cmd_start(
    message: Message,
    user: User,
    bot: Bot,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    subscribed = await is_subscribed(bot, message.from_user.id, settings.telegram_channel_id)

    if not subscribed:
        if user.is_subscribed:
            user.is_subscribed = False
            await db.flush()
        await message.answer(
            _NOT_SUBSCRIBED.format(channel=settings.telegram_channel_id),
            reply_markup=subscription_kb(),
        )
        return

    if not user.is_subscribed:
        user.is_subscribed = True
        await db.flush()

    if user.onboarding_state == OnboardingState.completed:
        await message.answer(_ALREADY_DONE)
        return

    if user.onboarding_state == OnboardingState.new:
        await message.answer(_WELCOME, reply_markup=welcome_kb())
    else:
        await message.answer(_RESUME)
        from bot.handlers.onboarding import resume_onboarding
        await resume_onboarding(message, user, state)


@router.callback_query(lambda c: c.data == "check_subscription")
async def check_subscription_callback(
    callback: CallbackQuery,
    user: User,
    bot: Bot,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    await callback.answer()

    subscribed = await is_subscribed(bot, callback.from_user.id, settings.telegram_channel_id)

    if not subscribed:
        await callback.message.answer(
            "Не вижу подписки. Подпишись на канал и попробуй снова.",
            reply_markup=subscription_kb(),
        )
        return

    if not user.is_subscribed:
        user.is_subscribed = True
        await db.flush()

    if user.onboarding_state == OnboardingState.completed:
        await callback.message.answer(_ALREADY_DONE)
        return

    await callback.message.answer(_WELCOME, reply_markup=welcome_kb())


@router.callback_query(lambda c: c.data == "start_onboarding")
async def start_onboarding_callback(
    callback: CallbackQuery,
    user: User,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    await callback.answer()
    from bot.handlers.onboarding import ask_name
    await ask_name(callback.message, user, state, db)
