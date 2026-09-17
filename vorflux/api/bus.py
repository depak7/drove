"""In-process pub/sub so a running job's events reach every connected browser.

Subscribers are bounded queues. A browser tab that stops reading — backgrounded, throttled, or
gone without closing the connection — must never make the engine block or grow memory without
limit, so a full queue drops its oldest events rather than applying back-pressure to the run.
Losing log lines for a stalled tab is fine; stalling the run behind it is not.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Iterator
from typing import Any

QUEUE_LIMIT = 1000


class Bus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the daemon's loop so publish() works from worker threads."""
        self._loop = loop

    def publish(self, event: dict[str, Any]) -> None:
        # FastAPI runs `def` routes in a threadpool, and asyncio.Queue is not thread-safe, so a
        # publish from a route has to be hopped onto the loop rather than touching queues here.
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not loop:
                loop.call_soon_threadsafe(self._deliver, event)
                return
        self._deliver(event)

    def _deliver(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    @contextlib.contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        with self.subscribe() as queue:
            while True:
                yield await queue.get()


bus = Bus()
