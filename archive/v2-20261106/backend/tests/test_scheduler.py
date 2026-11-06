import asyncio

import pytest
from sqlalchemy import select

from app.jobs.scheduler import JobDefinition, Scheduler
from app.models.job_run import JobRun


async def make_started_scheduler(session_factory) -> Scheduler:
    scheduler = Scheduler()
    await scheduler.start(session_factory)
    return scheduler


async def test_run_job_records_success(session_factory):
    scheduler = Scheduler()

    async def job():
        return 42

    definition = JobDefinition(name="test.ok", interval_seconds=3600, func=job)
    scheduler.register(definition)
    await scheduler.start(session_factory)
    try:
        run = await scheduler.run_job(definition)
    finally:
        await scheduler.stop()

    assert run.status == "succeeded"
    assert run.items == 42
    assert run.finished_at is not None
    assert definition.last_status == "succeeded"


async def test_run_job_records_failure_without_raising(session_factory):
    scheduler = Scheduler()

    async def job():
        raise RuntimeError("exchange exploded")

    definition = JobDefinition(name="test.fail", interval_seconds=3600, func=job)
    scheduler.register(definition)
    await scheduler.start(session_factory)
    try:
        run = await scheduler.run_job(definition)
    finally:
        await scheduler.stop()

    assert run.status == "failed"
    assert "exchange exploded" in run.error


async def test_scheduled_loop_executes(session_factory):
    scheduler = Scheduler()
    executions = asyncio.Event()

    async def job():
        executions.set()
        return 1

    scheduler.register(
        JobDefinition(name="test.loop", interval_seconds=3600, func=job, run_on_start=True)
    )
    await scheduler.start(session_factory)
    try:
        await asyncio.wait_for(executions.wait(), timeout=2)
    finally:
        await scheduler.stop()

    async with session_factory() as session:
        runs = (await session.execute(select(JobRun))).scalars().all()
    assert len(runs) >= 1
    assert runs[0].job_name == "test.loop"


async def test_duplicate_registration_rejected():
    scheduler = Scheduler()

    async def job():
        return None

    scheduler.register(JobDefinition(name="dup", interval_seconds=1, func=job))
    with pytest.raises(ValueError):
        scheduler.register(JobDefinition(name="dup", interval_seconds=1, func=job))


async def test_scope_key_isolates_accounts():
    scheduler = Scheduler()

    async def job():
        return None

    a = JobDefinition(name="sync", interval_seconds=1, func=job, exchange="FAKE", account_ref="a1")
    b = JobDefinition(name="sync", interval_seconds=1, func=job, exchange="FAKE", account_ref="a2")
    scheduler.register(a)
    scheduler.register(b)
    assert {j.key for j in scheduler.jobs()} == {"sync:FAKE:a1", "sync:FAKE:a2"}
