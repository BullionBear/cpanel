"""Execution records for scheduled jobs (sync health observability)."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.audit_log import BigIntPk
from app.models.base import Base


class JobRun(Base):
    __tablename__ = "job_run"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)

    job_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Optional scope; jobs are defined per (job, exchange, account)
    exchange: Mapped[str | None] = mapped_column(String(32))
    account_ref: Mapped[str | None] = mapped_column(String(64))

    # "running" -> "succeeded" | "failed"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
