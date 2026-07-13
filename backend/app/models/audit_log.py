"""Append-only audit trail for state-changing actions.

A row is inserted when an action starts and finalized (status, error,
finished_at) when it completes. Rows are never edited afterwards and never
deleted. Status/actor values are plain strings (see app.core.audit for the
canonical values) to keep migrations free of native enum churn.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# BIGINT pk on postgres, plain INTEGER on sqlite so autoincrement works in tests
BigIntPk = BigInteger().with_variant(Integer(), "sqlite")
JsonDict = JSON().with_variant(JSONB(), "postgresql")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)

    # Who: "user", "agent", or "system"
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_name: Mapped[str | None] = mapped_column(String(128))

    # What: dotted action name (e.g. "jobs.trigger", "orders.place") and target
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target: Mapped[str | None] = mapped_column(String(256))
    params: Mapped[dict | None] = mapped_column(JsonDict)

    # Outcome: "started" -> "succeeded" | "failed"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="started")
    error: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
