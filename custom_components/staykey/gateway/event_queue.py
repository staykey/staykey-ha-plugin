"""In-memory event queue for offline resilience.

Buffers events when the gateway connection is down and replays them on reconnect.
No file persistence - events are lost on HA restart (acceptable trade-off for simplicity).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Dict, Tuple

LOGGER = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 500
MAX_EVENT_AGE_S = 3600


class EventQueue:
    """Buffers events during gateway disconnections."""

    def __init__(self, max_size: int = MAX_QUEUE_SIZE) -> None:
        self._queue: deque[Tuple[float, str]] = deque(maxlen=max_size)
        self._dropped = 0

    @property
    def size(self) -> int:
        return len(self._queue)

    @property
    def dropped_count(self) -> int:
        return self._dropped

    def enqueue(self, message: str) -> None:
        """Add a message to the queue. Oldest messages are dropped if full."""
        if len(self._queue) >= self._queue.maxlen:
            self._dropped += 1

        self._queue.append((time.monotonic(), message))

    async def drain(self, send_fn) -> int:
        """Send all queued messages via the provided send function.

        Returns the number of messages successfully sent.
        """
        sent = 0
        now = time.monotonic()

        while self._queue:
            timestamp, message = self._queue[0]

            if now - timestamp > MAX_EVENT_AGE_S:
                self._queue.popleft()
                LOGGER.debug("Dropped stale queued event (age > %ds)", MAX_EVENT_AGE_S)
                continue

            try:
                await send_fn(message)
                self._queue.popleft()
                sent += 1
            except Exception:
                LOGGER.warning("Failed to send queued event, stopping drain")
                break

        if sent > 0:
            LOGGER.info("Drained %d queued events (%d remaining)", sent, len(self._queue))

        self._dropped = 0
        return sent

    def clear(self) -> None:
        self._queue.clear()
        self._dropped = 0
