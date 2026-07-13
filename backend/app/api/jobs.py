"""Scheduler observability and manual job triggering."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import Actor, audit, current_actor
from app.core.db import get_db
from app.jobs.scheduler import scheduler
from app.models.job_run import JobRun

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(current_actor)])


class JobInfo(BaseModel):
    key: str
    name: str
    exchange: str | None
    account_ref: str | None
    interval_seconds: float
    last_status: str | None
    last_run_at: datetime | None


class JobRunInfo(BaseModel):
    id: int
    job_name: str
    exchange: str | None
    account_ref: str | None
    status: str
    items: int
    error: str | None
    started_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[JobInfo])
async def list_jobs():
    return [
        JobInfo(
            key=j.key,
            name=j.name,
            exchange=j.exchange,
            account_ref=j.account_ref,
            interval_seconds=j.interval_seconds,
            last_status=j.last_status,
            last_run_at=j.last_run_at,
        )
        for j in scheduler.jobs()
    ]


@router.get("/runs", response_model=list[JobRunInfo])
async def list_runs(
    job_name: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(JobRun).order_by(JobRun.started_at.desc(), JobRun.id.desc())
    if job_name:
        stmt = stmt.where(JobRun.job_name == job_name)
    if status:
        stmt = stmt.where(JobRun.status == status)
    result = await db.execute(stmt.limit(limit))
    return [JobRunInfo.model_validate(row) for row in result.scalars()]


@router.post("/{key}/trigger", response_model=JobRunInfo)
async def trigger_job(
    key: str,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    try:
        job = scheduler.get(key)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown job {key!r}")
    if not scheduler.running:
        raise HTTPException(status_code=409, detail="Scheduler is not running")

    async with audit(db, actor, "jobs.trigger", target=key):
        run = await scheduler.run_job(job)
    return JobRunInfo.model_validate(run)
