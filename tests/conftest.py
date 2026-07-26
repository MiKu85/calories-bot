"""
Pytest configuration and shared fixtures.

Tests use a real PostgreSQL database (not mocks) to prevent divergence
between test behaviour and production migrations.
Set TEST_DATABASE_URL in .env.test or environment before running tests.
"""
from __future__ import annotations

import os

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.db.models import Base

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://calories_bot_user:calories_bot_pass_2026@localhost:5432/calories_bot_test",
)


# Function-scoped engine: keeps every test's fixtures and body on the same event
# loop that pytest-asyncio (0.24, auto mode) creates per test. A session-scoped
# engine plus the old custom `event_loop` fixture bound async work to a different
# loop → "attached to a different loop". Schema is (re)created per test; the suite
# is small, so the cost is negligible and isolation is stronger.
@pytest_asyncio.fixture
async def engine():
    _engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield _engine
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await _engine.dispose()


@pytest_asyncio.fixture
async def db(engine) -> AsyncSession:
    """Provides a transactional test session that is rolled back after each test."""
    async with engine.begin() as conn:
        session_factory = async_sessionmaker(bind=conn, expire_on_commit=False)
        async with session_factory() as session:
            yield session
            await session.rollback()
