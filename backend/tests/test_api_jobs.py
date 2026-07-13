"""End-to-end API tests for job health and audited manual triggering."""
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

import app.api.audit as audit_api
import app.api.jobs as jobs_api
from app.core.audit import Actor, current_actor
from app.core.db import get_db
from app.jobs.scheduler import JobDefinition, Scheduler
from app.main import app
from app.models.audit_log import AuditLog

TEST_ACTOR = Actor(type="user", id="42", name="tester")


@pytest_asyncio.fixture
async def client(session_factory, monkeypatch):
    test_scheduler = Scheduler()

    async def ok_job():
        return 7

    async def bad_job():
        raise RuntimeError("venue down")

    test_scheduler.register(
        JobDefinition(name="test.ok", interval_seconds=3600, func=ok_job, exchange="FAKE")
    )
    test_scheduler.register(JobDefinition(name="test.bad", interval_seconds=3600, func=bad_job))
    await test_scheduler.start(session_factory)

    monkeypatch.setattr(jobs_api, "scheduler", test_scheduler)

    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_actor] = lambda: TEST_ACTOR
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await test_scheduler.stop()


async def test_requires_auth(session_factory):
    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/jobs")).status_code == 401
        assert (await c.get("/audit")).status_code == 401
    app.dependency_overrides.clear()


async def test_list_jobs(client):
    response = await client.get("/jobs")
    assert response.status_code == 200
    keys = {j["key"] for j in response.json()}
    assert keys == {"test.ok:FAKE", "test.bad"}


async def test_trigger_success_and_run_visible(client):
    response = await client.post("/jobs/test.ok:FAKE/trigger")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["items"] == 7

    runs = (await client.get("/jobs/runs")).json()
    assert any(r["job_name"] == "test.ok" and r["status"] == "succeeded" for r in runs)


async def test_trigger_failure_recorded(client):
    response = await client.post("/jobs/test.bad/trigger")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "venue down" in response.json()["error"]


async def test_trigger_unknown_job_404(client):
    assert (await client.post("/jobs/nope/trigger")).status_code == 404


async def test_trigger_is_audited(client, session_factory):
    await client.post("/jobs/test.ok:FAKE/trigger")

    async with session_factory() as session:
        entries = (await session.execute(select(AuditLog))).scalars().all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == "jobs.trigger"
    assert entry.target == "test.ok:FAKE"
    assert entry.actor_id == TEST_ACTOR.id
    assert entry.status == "succeeded"

    listed = (await client.get("/audit")).json()
    assert listed[0]["action"] == "jobs.trigger"
