from __future__ import annotations

import asyncio

import structlog
import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from fastapi import FastAPI

from bot.db.session import AsyncSessionLocal
from bot.handlers.admin import router as admin_router
from bot.handlers.errors import router as errors_router
from bot.handlers.feedback import router as feedback_router
from bot.handlers.help import router as help_router
from bot.handlers.meal import router as meal_router
from bot.handlers.onboarding import router as onboarding_router
from bot.handlers.photo import router as photo_router
from bot.handlers.profile import router as profile_router
from bot.handlers.reset import router as reset_router
from bot.handlers.start import router as start_router
from bot.handlers.stats import router as stats_router
from bot.handlers.voice import router as voice_router
from bot.middleware.db import DbSessionMiddleware
from bot.middleware.user import UserMiddleware
from bot.tasks.feedback_scheduler import feedback_scheduler_loop
from bot.utils.logging import configure_logging
from config import settings

configure_logging(log_level=settings.log_level, production=settings.is_production)
logger = structlog.get_logger(__name__)


# ── Bot / Dispatcher factory ───────────────────────────────────────────────────

def create_bot() -> Bot:
    return Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())

    # Middleware order matters: DB session must be registered before User
    dp.update.middleware(DbSessionMiddleware())
    dp.update.middleware(UserMiddleware())

    # Router registration order matters for priority
    dp.include_router(errors_router)    # global error handler — always first
    dp.include_router(start_router)
    dp.include_router(onboarding_router)
    dp.include_router(profile_router)
    dp.include_router(reset_router)
    dp.include_router(stats_router)
    dp.include_router(help_router)
    dp.include_router(admin_router)
    dp.include_router(feedback_router)  # before voice/meal: intercepts FSM states
    dp.include_router(voice_router)     # F.voice — before meal catch-all
    dp.include_router(photo_router)     # F.photo — before meal catch-all
    dp.include_router(meal_router)      # last: catches all remaining text

    return dp


# ── Polling mode (development) ─────────────────────────────────────────────────

async def run_polling() -> None:
    bot = create_bot()
    dp = create_dispatcher()
    asyncio.create_task(feedback_scheduler_loop(bot, AsyncSessionLocal))
    logger.info("bot_starting", mode="polling")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


# ── Webhook mode (production) ──────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(title="Calories Bot", docs_url=None, redoc_url=None)
    bot = create_bot()
    dp = create_dispatcher()

    @app.on_event("startup")
    async def on_startup() -> None:
        webhook_url = f"{settings.webhook_url}{settings.webhook_path}"
        await bot.set_webhook(webhook_url)
        asyncio.create_task(feedback_scheduler_loop(bot, AsyncSessionLocal))
        logger.info("webhook_set", url=webhook_url)

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        await bot.delete_webhook()
        await bot.session.close()
        logger.info("bot_shutdown")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=bot)

    return app


# ── Entrypoint ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if settings.use_webhook:
        app = create_app()
        uvicorn.run(app, host=settings.app_host, port=settings.app_port)
    else:
        asyncio.run(run_polling())
