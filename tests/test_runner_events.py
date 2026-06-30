import asyncio
import uuid

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.tables import Base, WebSource, CrawlJob
from app.models.enums import CrawlJobStatus
from app.workers.runner import process_crawl_job
from app.events import (
    InMemoryEventPublisher,
    TOPIC_CRAWL_STARTED, TOPIC_CRAWL_COMPLETED, TOPIC_CRAWL_FAILED,
    TOPIC_PATTERN_BUILT, TOPIC_PATTERN_APPROVED,
)


class _FakeWorker:
    def __init__(self, *, precheck_ok=True, postcheck_reason=None, text="<main>x</main>"):
        self._pre = precheck_ok
        self._post = postcheck_reason
        self._text = text

    def precheck(self, job):
        return {"ok": self._pre, "reason": None if self._pre else "bad"}

    def execute(self, job):
        return {"status": "succeeded", "url": job["url"], "text": self._text}

    def postcheck(self, result):
        return {"ok": True, "reason": None} if self._post is None else {"ok": False, "reason": self._post}


@pytest.fixture()
def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    asyncio.run(engine.dispose())


async def _make_source(session) -> uuid.UUID:
    sid = uuid.uuid4()
    session.add(WebSource(id=sid, url=f"https://ex.com/{sid}", domain="ex.com",
                          source_type="article", crawl_status="discovered", license_status="unknown"))
    await session.flush()
    return sid


async def _make_job(session, status=CrawlJobStatus.queued) -> CrawlJob:
    sid = await _make_source(session)
    job = CrawlJob(id=uuid.uuid4(), source_id=sid, url="https://ex.com/x", job_type="fetch_html", status=status.value)
    session.add(job)
    await session.flush()
    return job


def test_success_emits_started_built_approved_completed(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_job(session)
            pub = InMemoryEventPublisher()
            worker = _FakeWorker()
            out = await process_crawl_job(
                session,
                job={"url": "https://ex.com/p", "job_type": "fetch_html"},
                worker=worker, source_id=job.source_id, license_status="allowed",
                crawl_job=job, publisher=pub, trace_id="T1",
            )
            await session.commit()
            assert out["ok"] is True
            types = [e["event_type"] for t, e in pub.events]
            assert TOPIC_CRAWL_STARTED in types
            assert TOPIC_PATTERN_BUILT in types
            assert TOPIC_PATTERN_APPROVED in types
            assert TOPIC_CRAWL_COMPLETED in types
            started = pub.by_type(TOPIC_CRAWL_STARTED)[0]
            assert started["job_id"] == str(job.id)
            assert started["trace_id"] == "T1"
    asyncio.run(_t())


def test_precheck_fail_emits_crawl_failed(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_job(session)
            pub = InMemoryEventPublisher()
            worker = _FakeWorker(precheck_ok=False)
            await process_crawl_job(
                session,
                job={"url": "https://ex.com/p", "job_type": "fetch_html"},
                worker=worker, source_id=job.source_id, license_status="allowed",
                crawl_job=job, publisher=pub,
            )
            await session.commit()
            failed = pub.by_type(TOPIC_CRAWL_FAILED)
            assert len(failed) == 1
            assert failed[0]["payload"]["stage"] == "precheck"
            types = [e["event_type"] for t, e in pub.events]
            assert TOPIC_CRAWL_COMPLETED not in types
    asyncio.run(_t())


def test_postcheck_fail_emits_crawl_failed(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_job(session)
            pub = InMemoryEventPublisher()
            worker = _FakeWorker(postcheck_reason="too_large")
            await process_crawl_job(
                session,
                job={"url": "https://ex.com/p", "job_type": "fetch_html"},
                worker=worker, source_id=job.source_id, license_status="allowed",
                crawl_job=job, publisher=pub,
            )
            await session.commit()
            failed = pub.by_type(TOPIC_CRAWL_FAILED)
            assert len(failed) == 1
            assert failed[0]["payload"]["stage"] == "postcheck"
            assert failed[0]["payload"]["reason"] == "too_large"
    asyncio.run(_t())


def test_no_publisher_no_error(session_factory):
    async def _t():
        async with session_factory() as session:
            sid = await _make_source(session)
            worker = _FakeWorker()
            out = await process_crawl_job(
                session,
                job={"url": "https://ex.com/p", "job_type": "fetch_html"},
                worker=worker, source_id=sid, license_status="allowed",
            )
            await session.commit()
            assert out["ok"] is True
    asyncio.run(_t())