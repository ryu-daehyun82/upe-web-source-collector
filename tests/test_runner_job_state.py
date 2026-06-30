import asyncio
import uuid

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.tables import Base, WebSource, CrawlJob
from app.models.enums import CrawlJobStatus
from app.workers.runner import process_crawl_job


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
        if self._post is None:
            return {"ok": True, "reason": None}
        return {"ok": False, "reason": self._post}


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


def test_success_marks_succeeded(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_job(session)
            worker = _FakeWorker()
            out = await process_crawl_job(
                session,
                job={"url": "https://ex.com/p", "job_type": "fetch_html"},
                worker=worker, source_id=job.source_id, license_status="allowed", crawl_job=job,
            )
            await session.commit()
            assert out["ok"] is True
            assert job.status == "succeeded"
            assert job.started_at is not None
            assert job.finished_at is not None
    asyncio.run(_t())


def test_precheck_fail_marks_failed_terminal(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_job(session)
            worker = _FakeWorker(precheck_ok=False)
            out = await process_crawl_job(
                session,
                job={"url": "https://ex.com/p", "job_type": "fetch_html"},
                worker=worker, source_id=job.source_id, license_status="allowed", crawl_job=job,
            )
            await session.commit()
            assert out["stage"] == "precheck"
            assert job.status == "failed_terminal"
            assert job.error_code == "precheck"
    asyncio.run(_t())


def test_postcheck_fetch_failed_marks_retryable(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_job(session)
            worker = _FakeWorker(postcheck_reason="fetch_failed")
            out = await process_crawl_job(
                session,
                job={"url": "https://ex.com/p", "job_type": "fetch_html"},
                worker=worker, source_id=job.source_id, license_status="allowed", crawl_job=job,
            )
            await session.commit()
            assert out["stage"] == "postcheck"
            assert job.status == "failed_retryable"
            assert job.attempt_count == 1
    asyncio.run(_t())


def test_postcheck_too_large_marks_terminal(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_job(session)
            worker = _FakeWorker(postcheck_reason="too_large")
            out = await process_crawl_job(
                session,
                job={"url": "https://ex.com/p", "job_type": "fetch_html"},
                worker=worker, source_id=job.source_id, license_status="allowed", crawl_job=job,
            )
            await session.commit()
            assert job.status == "failed_terminal"
    asyncio.run(_t())


def test_no_crawl_job_still_works(session_factory):
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