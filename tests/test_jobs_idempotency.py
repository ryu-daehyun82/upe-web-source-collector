import asyncio
import uuid

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.tables import Base, WebSource
from app.jobs import make_idempotency_key, enqueue_crawl_job, get_job_by_idempotency_key


@pytest.fixture()
def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    asyncio.run(engine.dispose())


async def _make_source(session) -> uuid.UUID:
    sid = uuid.uuid4()
    session.add(WebSource(
        id=sid, url=f"https://ex.com/{sid}", domain="ex.com",
        source_type="article", crawl_status="discovered", license_status="unknown",
    ))
    await session.flush()
    return sid


def test_key_deterministic():
    sid = uuid.uuid4()
    key1 = make_idempotency_key(sid, "download_file", "h1")
    key2 = make_idempotency_key(sid, "download_file", "h1")
    assert key1 == key2
    assert len(key1) == 32
    assert all(c in "0123456789abcdef" for c in key1)


def test_key_differs_by_content_hash():
    sid = uuid.uuid4()
    key1 = make_idempotency_key(sid, "download_file", "h1")
    key2 = make_idempotency_key(sid, "download_file", "h2")
    assert key1 != key2


def test_key_differs_by_job_type():
    sid = uuid.uuid4()
    key1 = make_idempotency_key(sid, "a", "h1")
    key2 = make_idempotency_key(sid, "b", "h1")
    assert key1 != key2


def test_key_accepts_uuid_and_str():
    sid = uuid.uuid4()
    key1 = make_idempotency_key(sid, "download_file", "h1")
    key2 = make_idempotency_key(str(sid), "download_file", "h1")
    assert key1 == key2


def test_enqueue_creates_then_idempotent(session_factory):
    async def run():
        async with session_factory() as session:
            sid = await _make_source(session)
            job1, created1 = await enqueue_crawl_job(
                session, source_id=sid, url="https://ex.com/x",
                job_type="download_file", content_hash="h1",
            )
            assert created1 is True
            job2, created2 = await enqueue_crawl_job(
                session, source_id=sid, url="https://ex.com/x",
                job_type="download_file", content_hash="h1",
            )
            assert created2 is False
            assert job2.id == job1.id
            assert job1.status == "queued"

    asyncio.run(run())


def test_enqueue_different_content_hash_creates_new(session_factory):
    async def run():
        async with session_factory() as session:
            sid = await _make_source(session)
            job1, created1 = await enqueue_crawl_job(
                session, source_id=sid, url="https://ex.com/x",
                job_type="download_file", content_hash="h1",
            )
            assert created1 is True
            job2, created2 = await enqueue_crawl_job(
                session, source_id=sid, url="https://ex.com/x",
                job_type="download_file", content_hash="h2",
            )
            assert created2 is True
            assert job2.id != job1.id

    asyncio.run(run())


def test_get_by_key(session_factory):
    async def run():
        async with session_factory() as session:
            sid = await _make_source(session)
            job, _ = await enqueue_crawl_job(
                session, source_id=sid, url="https://ex.com/x",
                job_type="download_file", content_hash="h1",
            )
            key = make_idempotency_key(sid, "download_file", "h1")
            found = await get_job_by_idempotency_key(session, key)
            assert found is not None
            assert found.id == job.id
            not_found = await get_job_by_idempotency_key(session, "deadbeef" * 4)
            assert not_found is None

    asyncio.run(run())