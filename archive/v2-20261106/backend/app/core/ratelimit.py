"""Per-exchange rate-limit budgets with interactive-over-sync priority.

Every outgoing call to a venue acquires from that venue's token bucket.
Interactive traffic (order placement, user-triggered actions) is admitted
before queued background sync traffic when tokens are scarce.
"""
import asyncio
import heapq
import itertools
import time
from enum import IntEnum


class Priority(IntEnum):
    # Lower value = served first
    INTERACTIVE = 0
    SYNC = 1


class TokenBucket:
    """Async token bucket with strict priority ordering among waiters."""

    def __init__(self, rate: float, capacity: float):
        if rate <= 0 or capacity <= 0:
            raise ValueError("rate and capacity must be positive")
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._updated = time.monotonic()
        self._cond = asyncio.Condition()
        self._waiters: list[tuple[int, int, object]] = []  # (priority, seq, handle)
        self._seq = itertools.count()

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    async def acquire(self, weight: float = 1.0, priority: Priority = Priority.SYNC) -> None:
        if weight > self.capacity:
            raise ValueError(f"weight {weight} exceeds bucket capacity {self.capacity}")
        handle = object()
        async with self._cond:
            heapq.heappush(self._waiters, (int(priority), next(self._seq), handle))
            try:
                while True:
                    self._refill()
                    if self._waiters[0][2] is handle and self._tokens >= weight:
                        heapq.heappop(self._waiters)
                        self._tokens -= weight
                        self._cond.notify_all()
                        return
                    # Wake up when more tokens should be available.
                    timeout = max((weight - self._tokens) / self.rate, 0.005)
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=timeout)
                    except TimeoutError:
                        pass
            except BaseException:
                self._waiters = [w for w in self._waiters if w[2] is not handle]
                heapq.heapify(self._waiters)
                self._cond.notify_all()
                raise


class RateLimiterRegistry:
    """One bucket per exchange; budgets are configured once at connector setup."""

    def __init__(self):
        self._buckets: dict[str, TokenBucket] = {}

    def configure(self, exchange: str, rate: float, capacity: float) -> TokenBucket:
        bucket = TokenBucket(rate=rate, capacity=capacity)
        self._buckets[exchange.upper()] = bucket
        return bucket

    def get(self, exchange: str) -> TokenBucket:
        try:
            return self._buckets[exchange.upper()]
        except KeyError:
            raise KeyError(f"No rate limit configured for exchange {exchange!r}") from None


rate_limiters = RateLimiterRegistry()
