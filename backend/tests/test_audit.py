import pytest
from sqlalchemy import select

from app.core.audit import SYSTEM_ACTOR, Actor, audit
from app.models.audit_log import AuditLog


async def test_successful_action_recorded(db):
    actor = Actor(type="user", id="123", name="alice")
    async with audit(db, actor, "jobs.trigger", target="demo.heartbeat", params={"x": 1}):
        pass

    rows = (await db.execute(select(AuditLog))).scalars().all()
    assert len(rows) == 1
    entry = rows[0]
    assert entry.status == "succeeded"
    assert entry.actor_id == "123"
    assert entry.action == "jobs.trigger"
    assert entry.params == {"x": 1}
    assert entry.finished_at is not None


async def test_failed_action_recorded_and_reraised(db):
    with pytest.raises(RuntimeError):
        async with audit(db, SYSTEM_ACTOR, "orders.place", target="FAKE_SPOT_BTCUSDT"):
            raise RuntimeError("filter violation")

    rows = (await db.execute(select(AuditLog))).scalars().all()
    assert len(rows) == 1
    entry = rows[0]
    assert entry.status == "failed"
    assert "filter violation" in entry.error


async def test_failure_rolls_back_action_but_keeps_audit(db):
    """The action's writes roll back; the failure record persists."""
    with pytest.raises(RuntimeError):
        async with audit(db, SYSTEM_ACTOR, "test.action"):
            db.add(
                AuditLog(
                    actor_type="system", actor_id="stray", action="should.not.persist",
                    status="started",
                )
            )
            raise RuntimeError("boom")

    rows = (await db.execute(select(AuditLog))).scalars().all()
    assert [r.action for r in rows] == ["test.action"]
