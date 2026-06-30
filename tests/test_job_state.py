import asyncio
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.tables import Base, WebSource, CrawlJob
from app.models.enums import CrawlJobStatus
from app.job_state import (
    can_transition, transition_job, InvalidTransition, TERMINAL_STATES,
    mark_running, mark_succeeded, mark_failed_retryable, mark_failed_terminal, mark_cancelled,
)


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


def test_can_transition_basic():
    assert can_transition(CrawlJobStatus.queued, CrawlJobStatus.running) is True
    assert can_transition(CrawlJobStatus.succeeded, CrawlJobStatus.running) is False
    assert can_transition(CrawlJobStatus.running, CrawlJobStatus.succeeded) is True
    assert can_transition(CrawlJobStatus.ready, CrawlJobStatus.running) is True
    assert can_transition(CrawlJobStatus.queued, CrawlJobStatus.queued) is False
    assert can_transition(CrawlJobStatus.running, CrawlJobStatus.failed_retryable) is True
    assert can_transition(CrawlJobStatus.failed_retryable, CrawlJobStatus.running) is True


def test_transition_to_running_sets_started(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_job(session)
            await mark_running(session, job)
            await session.commit()
            assert job.status == "running"
            assert job.started_at is not None
    asyncio.run(_t())


def test_running_to_succeeded_sets_finished(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_job(session)
            await mark_running(session, job)
            await mark_succeeded(session, job)
            await session.commit()
            assert job.status == "succeeded"
            assert job.finished_at is not None
    asyncio.run(_t())


def test_failed_retryable_increments_attempt(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_job(session)
            await mark_running(session, job)
            await mark_failed_retryable(session, job, error_code="e", error_message="m")
            await session.commit()
            assert job.status == "failed_retryable"
            assert job.attempt_count == 1
            # failed_retryable는 비터미널 → finished_at 미설정(재시도 가능).
            assert job.finished_at is None
            assert job.error_code == "e"
            assert job.error_message == "m"
    asyncio.run(_t())


def test_invalid_transition_raises(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_job(session)
            await mark_running(session, job)
            await mark_succeeded(session, job)
            await session.commit()
            with pytest.raises(InvalidTransition):
                await transition_job(session, job, CrawlJobStatus.running)
    asyncio.run(_t())


def test_now_injection(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_job(session)
            fixed = datetime(2026, 1, 1, tzinfo=timezone.utc)
            await mark_running(session, job, now=fixed)
            await session.commit()
            assert job.started_at == fixed
    asyncio.run(_t())


def test_cancelled_terminal(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_job(session)
            await mark_cancelled(session, job)
            await session.commit()
            assert job.status == "cancelled"
            assert job.finished_at is not None
            assert CrawlJobStatus.cancelled in TERMINAL_STATES
    asyncio.run(_t())


def test_mark_failed_terminal(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_job(session)
            await mark_running(session, job)
            await mark_failed_terminal(session, job, error_code="x")
            await session.commit()
            assert job.status == "failed_terminal"
            assert job.error_code == "x"
            assert job.finished_at is not None
    asyncio.run(_t())