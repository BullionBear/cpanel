import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    """Fresh file-backed SQLite database per test.

    File-backed (not :memory:) so every pooled connection — including ones
    opened by scheduler background tasks — sees the same schema.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def db(session_factory):
    async with session_factory() as session:
        yield session
