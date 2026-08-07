"""In-process pub/sub for panel realtime notifications (SSE).

One hub per API process — same deployment model as the asyncio scheduler.
Subscribers are per-SSE-connection queues; publishers never block on slow
clients (full queues drop the oldest event).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "BalanceUpdatedEvent",
    "EventHub",
    "event_hub",
]


@dataclass(frozen=True)
class BalanceUpdatedEvent:
    account_id: int
    exchange: str
    label: str
    reason: str
    taken_at: datetime | None = None
    type: str = "balance.updated"

    def as_sse_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.taken_at is not None:
            payload["taken_at"] = self.taken_at.isoformat()
        return payload


class EventHub:
    def __init__(self, *, queue_size: int = 64) -> None:
        self._queue_size = queue_size
        self._subs: set[asyncio.Queue[BalanceUpdatedEvent]] = set()

    def subscribe(self) -> asyncio.Queue[BalanceUpdatedEvent]:
        queue: asyncio.Queue[BalanceUpdatedEvent] = asyncio.Queue(maxsize=self._queue_size)
        self._subs.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[BalanceUpdatedEvent]) -> None:
        self._subs.discard(queue)

    def subscriber_count(self) -> int:
        return len(self._subs)

    def publish(self, event: BalanceUpdatedEvent) -> None:
        dead: list[asyncio.Queue[BalanceUpdatedEvent]] = []
        for queue in self._subs:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning("Dropping %s for a slow SSE subscriber", event.type)
            except Exception:
                logger.exception("Failed to enqueue event for subscriber")
                dead.append(queue)
        for queue in dead:
            self._subs.discard(queue)

    def clear(self) -> None:
        """Drop all subscribers (tests / shutdown)."""
        self._subs.clear()


event_hub = EventHub()
