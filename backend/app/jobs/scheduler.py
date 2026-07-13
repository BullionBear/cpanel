"""Async job scheduler with persisted run records.

Jobs are registered with a name, an interval, and an async function taking
no arguments and returning the number of items processed (or None). Each
execution writes a JobRun row (running -> succeeded/failed) so sync health
is observable through the API.

Jobs are scoped per (job, exchange, account) by registering one definition
per scope — a slow venue or account never blocks the others because every
definition runs on its own task.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.job_run import JobRun

logger = logging.getLogger(__name__)

JobFunc = Callable[[], Awaitable[int | None]]


@dataclass
class JobDefinition:
    name: str
    interval_seconds: float
    func: JobFunc
    exchange: str | None = None
    account_ref: str | None = None
    run_on_start: bool = False
    # Runtime state (not persisted; JobRun rows are the durable record)
    last_status: str | None = field(default=None, init=False)
    last_run_at: datetime | None = field(default=None, init=False)

    @property
    def key(self) -> str:
        parts = [self.name]
        if self.exchange:
            parts.append(self.exchange)
        if self.account_ref:
            parts.append(self.account_ref)
        return ":".join(parts)


class Scheduler:
    def __init__(self):
        self._jobs: dict[str, JobDefinition] = {}
        self._tasks: list[asyncio.Task] = []
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def register(self, job: JobDefinition) -> None:
        if job.key in self._jobs:
            raise ValueError(f"Job {job.key!r} is already registered")
        self._jobs[job.key] = job

    def jobs(self) -> list[JobDefinition]:
        return list(self._jobs.values())

    def get(self, key: str) -> JobDefinition:
        try:
            return self._jobs[key]
        except KeyError:
            raise KeyError(f"No job registered with key {key!r}") from None

    async def start(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        if self._running:
            return
        self._session_factory = session_factory
        self._running = True
        for job in self._jobs.values():
            self._tasks.append(asyncio.create_task(self._loop(job), name=f"job:{job.key}"))
        logger.info("Scheduler started with %d job(s)", len(self._jobs))

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("Scheduler stopped")

    async def _loop(self, job: JobDefinition) -> None:
        if not job.run_on_start:
            await asyncio.sleep(job.interval_seconds)
        while self._running:
            await self.run_job(job)
            await asyncio.sleep(job.interval_seconds)

    async def run_job(self, job: JobDefinition) -> JobRun:
        """Execute one job now, recording the run. Never raises."""
        assert self._session_factory is not None, "scheduler not started"
        async with self._session_factory() as session:
            run = JobRun(
                job_name=job.name,
                exchange=job.exchange,
                account_ref=job.account_ref,
                status="running",
            )
            session.add(run)
            await session.commit()
            run_id = run.id

        try:
            items = await job.func()
            status, error = "succeeded", None
        except asyncio.CancelledError:
            status, error, items = "failed", "cancelled (shutdown)", None
            await self._finalize(run_id, status, items, error)
            raise
        except Exception as exc:
            logger.exception("Job %s failed", job.key)
            status, error, items = "failed", str(exc)[:2000], None

        job.last_status = status
        job.last_run_at = datetime.now(timezone.utc)
        return await self._finalize(run_id, status, items, error)

    async def _finalize(
        self, run_id: int, status: str, items: int | None, error: str | None
    ) -> JobRun:
        assert self._session_factory is not None
        async with self._session_factory() as session:
            run = await session.get(JobRun, run_id)
            assert run is not None
            run.status = status
            run.items = items or 0
            run.error = error
            run.finished_at = datetime.now(timezone.utc)
            await session.commit()
            return run


scheduler = Scheduler()
