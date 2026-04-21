"""
Meal message debounce service — Layer 1 of multimodal message merging (Task 5).

Problem: users send a photo and then, within 2-60 seconds, send a voice or
text clarification about the same dish. Without buffering, the bot treats
them as separate meals and double-counts the daily tracker.

Solution: hold incoming messages in an in-memory buffer per user. Each new
message resets a silence timer. When the timer fires (or a hard limit is hit),
all buffered messages are sent to the LLM as a single batch request.

State is intentionally kept in process memory only — not in PostgreSQL or
Redis. This is a hot path (written every few seconds) and the buffer is
ephemeral: on process restart any in-flight buffer is discarded, which is
acceptable because the user would simply re-send.

Buffer lifecycle:
  message received → no active session → create session, start timer + typing loop
  message received → active session     → add to buffer, reset timer
  MAX_MESSAGES or MAX_TOTAL reached     → immediate flush (no wait)
  timer fires                           → flush buffer → session closed
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import structlog

if TYPE_CHECKING:
    from aiogram import Bot

logger = structlog.get_logger(__name__)

# How often to re-send "typing" action so Telegram keeps showing the indicator.
# Telegram auto-hides it after ~5 s, so we refresh every 4 s.
_TYPING_INTERVAL = 4.0


@dataclass
class BufferedMessage:
    """Single message held in an accumulation session."""

    kind: Literal["photo", "voice", "text"]
    timestamp: float = field(default_factory=time.monotonic)

    # ── Photo ─────────────────────────────────────────────────────────────────
    image_bytes: bytes | None = None
    mime_type: str = "image/jpeg"
    caption: str | None = None

    # ── Voice ─────────────────────────────────────────────────────────────────
    audio_bytes: bytes | None = None
    audio_mime: str = "audio/ogg"
    # STT is kicked off immediately on receipt (parallel to buffering) so the
    # transcript is ready by the time the timer fires.
    stt_task: asyncio.Task | None = None
    # Filled when stt_task completes; None means not yet done or failed.
    stt_result: str | None = None

    # ── Text ──────────────────────────────────────────────────────────────────
    text: str | None = None


@dataclass
class _BufferState:
    telegram_id: int
    chat_id: int
    messages: list[BufferedMessage] = field(default_factory=list)
    first_message_time: float = field(default_factory=time.monotonic)
    debounce_task: asyncio.Task | None = None
    typing_task: asyncio.Task | None = None


# Type alias for the flush callback registered by main.py.
# Signature: flush(telegram_id, chat_id, messages) -> None
FlushCallback = Callable[[int, int, list[BufferedMessage]], Awaitable[None]]


class MealDebounceService:
    """
    In-process debounce buffer for meal messages.

    Call `init()` once at startup (from main.py) before any messages arrive.
    Call `add_message()` from every photo/voice/text meal handler instead of
    processing immediately.
    """

    def __init__(self) -> None:
        self._buffers: dict[int, _BufferState] = {}
        self._bot: "Bot | None" = None
        self._flush_callback: FlushCallback | None = None
        self._debounce_seconds: int = 12
        self._max_total_seconds: int = 90
        self._max_messages: int = 5
        self._fsm_storage = None  # set via init(); used by flush_meal_buffer to set FSM state

    # ── Initialisation (called once from main.py) ─────────────────────────────

    def init(
        self,
        bot: "Bot",
        flush_callback: FlushCallback,
        *,
        debounce_seconds: int = 12,
        max_total_seconds: int = 90,
        max_messages: int = 5,
        fsm_storage=None,
    ) -> None:
        self._bot = bot
        self._flush_callback = flush_callback
        self._debounce_seconds = debounce_seconds
        self._max_total_seconds = max_total_seconds
        self._max_messages = max_messages
        self._fsm_storage = fsm_storage

    # ── Public API ────────────────────────────────────────────────────────────

    async def add_message(
        self,
        telegram_id: int,
        chat_id: int,
        msg: BufferedMessage,
    ) -> None:
        """
        Add a message to the user's accumulation session.

        Creates a new session if none exists; resets the debounce timer if one
        does. Triggers an immediate flush when hard limits are hit.
        """
        assert self._bot is not None, "MealDebounceService.init() was not called"
        assert self._flush_callback is not None

        log = logger.bind(telegram_id=telegram_id, kind=msg.kind)

        if telegram_id not in self._buffers:
            state = _BufferState(telegram_id=telegram_id, chat_id=chat_id)
            self._buffers[telegram_id] = state
            state.messages.append(msg)
            log.info("debounce_session_started", msg_count=1)

            state.typing_task = asyncio.create_task(
                self._typing_loop(telegram_id, chat_id),
                name=f"typing_{telegram_id}",
            )
            state.debounce_task = asyncio.create_task(
                self._run_debounce(telegram_id, chat_id),
                name=f"debounce_{telegram_id}",
            )
            return

        state = self._buffers[telegram_id]
        state.messages.append(msg)
        count = len(state.messages)
        elapsed = time.monotonic() - state.first_message_time
        log.info("debounce_message_added", msg_count=count, elapsed_s=round(elapsed, 1))

        # Hard limit: too many messages
        if count >= self._max_messages:
            log.info("debounce_flush_reason", reason="max_messages", count=count)
            await self._immediate_flush(telegram_id, chat_id, reason="max_messages")
            return

        # Hard limit: total window exceeded
        if elapsed >= self._max_total_seconds:
            log.info("debounce_flush_reason", reason="max_total", elapsed_s=round(elapsed, 1))
            await self._immediate_flush(telegram_id, chat_id, reason="max_total")
            return

        # Reset silence timer
        if state.debounce_task and not state.debounce_task.done():
            state.debounce_task.cancel()
        state.debounce_task = asyncio.create_task(
            self._run_debounce(telegram_id, chat_id),
            name=f"debounce_{telegram_id}",
        )

    # ── Internal timer / flush helpers ────────────────────────────────────────

    async def _run_debounce(self, telegram_id: int, chat_id: int) -> None:
        """Sleep for the silence window, then flush."""
        try:
            await asyncio.sleep(self._debounce_seconds)
        except asyncio.CancelledError:
            return
        await self._do_flush(telegram_id, chat_id, reason="timeout")

    async def _immediate_flush(
        self, telegram_id: int, chat_id: int, reason: str
    ) -> None:
        state = self._buffers.get(telegram_id)
        if state is None:
            return
        if state.debounce_task and not state.debounce_task.done():
            state.debounce_task.cancel()
        await self._do_flush(telegram_id, chat_id, reason=reason)

    async def _do_flush(
        self, telegram_id: int, chat_id: int, reason: str
    ) -> None:
        """Pop the buffer, stop the typing loop, invoke the flush callback."""
        state = self._buffers.pop(telegram_id, None)
        if state is None:
            return

        if state.typing_task and not state.typing_task.done():
            state.typing_task.cancel()

        messages = state.messages
        log = logger.bind(
            telegram_id=telegram_id,
            msg_count=len(messages),
            reason=reason,
            kinds=[m.kind for m in messages],
        )
        log.info("debounce_flushing")

        try:
            await self._flush_callback(telegram_id, chat_id, messages)  # type: ignore[misc]
        except Exception as exc:
            log.error("debounce_flush_error", error=str(exc), exc_info=True)

    async def _typing_loop(self, telegram_id: int, chat_id: int) -> None:
        """Send 'typing' action every _TYPING_INTERVAL seconds until cancelled."""
        assert self._bot is not None
        try:
            while True:
                try:
                    await self._bot.send_chat_action(chat_id=chat_id, action="typing")
                except Exception as exc:
                    logger.warning(
                        "typing_action_failed", telegram_id=telegram_id, error=str(exc)
                    )
                await asyncio.sleep(_TYPING_INTERVAL)
        except asyncio.CancelledError:
            pass


# ── Module-level singleton — initialised in main.py ──────────────────────────
meal_debounce_service = MealDebounceService()
