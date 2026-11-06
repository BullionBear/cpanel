import asyncio

import pytest

from app.core.ratelimit import Priority, RateLimiterRegistry, TokenBucket


async def test_immediate_acquire_within_capacity():
    bucket = TokenBucket(rate=10, capacity=5)
    await asyncio.wait_for(bucket.acquire(weight=5), timeout=0.1)


async def test_waits_for_refill():
    bucket = TokenBucket(rate=100, capacity=1)
    await bucket.acquire()  # drain
    loop = asyncio.get_running_loop()
    start = loop.time()
    await bucket.acquire()  # must wait ~10ms for refill
    assert loop.time() - start >= 0.005


async def test_interactive_served_before_sync():
    bucket = TokenBucket(rate=50, capacity=1)
    await bucket.acquire()  # drain so both waiters queue up

    order: list[str] = []

    async def worker(label: str, priority: Priority):
        await bucket.acquire(priority=priority)
        order.append(label)

    sync_task = asyncio.create_task(worker("sync", Priority.SYNC))
    await asyncio.sleep(0.001)  # sync waiter queues first
    interactive_task = asyncio.create_task(worker("interactive", Priority.INTERACTIVE))
    await asyncio.gather(sync_task, interactive_task)

    assert order == ["interactive", "sync"]


async def test_cancelled_waiter_does_not_block_bucket():
    bucket = TokenBucket(rate=100, capacity=1)
    await bucket.acquire()

    task = asyncio.create_task(bucket.acquire(priority=Priority.INTERACTIVE))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.wait_for(bucket.acquire(priority=Priority.SYNC), timeout=1)


async def test_weight_larger_than_capacity_rejected():
    bucket = TokenBucket(rate=1, capacity=2)
    with pytest.raises(ValueError):
        await bucket.acquire(weight=3)


def test_registry():
    registry = RateLimiterRegistry()
    registry.configure("binance", rate=20, capacity=40)
    assert registry.get("BINANCE") is registry.get("binance")
    with pytest.raises(KeyError):
        registry.get("UNKNOWN")
