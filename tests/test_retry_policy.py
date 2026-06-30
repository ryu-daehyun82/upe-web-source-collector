import asyncio
import uuid

import pytest

from app.retry_policy import (
    decide_retry, schedule_retry_or_fail,
    RETRY_POLICY, REASON_TO_ERROR_CODE, MAX_DELAY_MS,
)

pytest.importorskip("aiosqlite")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.tables import Base, WebSource, CrawlJob
from app.models.enums import CrawlJobStatus
from app.job_state import mark_running


# ── decide_retry (순수, DB 불필요) ────────────────────────────────

def test_network_timeout_backoff_sequence():
    d0 = decide_retry("network_timeout", 0)
    assert d0.should_retry is True and d0.delay_ms == 1000 and d0.next_attempt == 1
    assert decide_retry("network_timeout", 1).delay_ms == 2000
    assert decide_retry("network_timeout", 2).delay_ms == 4000


def test_network_timeout_exhausted():
    d = decide_retry("network_timeout", 3)
    assert d.should_retry is False
    assert d.terminal_reason == "network_exhausted"


def test_forbidden_no_retry():
    d = decide_retry("forbidden", 0)
    assert d.should_retry is False
    assert d.terminal_reason == "forbidden"


def test_content_too_large_no_retry():
    assert decide_retry("content_too_large", 0).should_retry is False


def test_pii_detected_no_retry():
    d = decide_retry("pii_detected", 0)
    assert d.should_retry is False
    assert d.terminal_reason == "blocked_sensitive"


def test_rate_limited_retries():
    d = decide_retry("rate_limited", 0)
    assert d.should_retry is True and d.delay_ms == 5000
    assert decide_retry("rate_limited", 5).should_retry is False


def test_parser_error_two_retries():
    assert decide_retry("parser_error", 0).should_retry is True
    assert decide_retry("parser_error", 1).should_retry is True
    assert decide_retry("parser_error", 2).should_retry is False


def test_unknown_error_defaults_terminal():
    d = decide_retry("nonsense_code", 0)
    assert d.should_retry is False
    assert d.terminal_reason == "unknown_error"


def test_backoff_capped_by_max_delay():
    # rate_limited(max 5) attempt 4 → 5000*2^4=80000, max_delay 10000 으로 상한
    d = decide_retry("rate_limited", 4, max_delay_ms=10000)
    assert d.should_retry is True
    assert d.delay_ms == 10000


def test_reason_to_error_code_mapping():
    assert REASON_TO_ERROR_CODE["fetch_failed"] == "network_timeout"
    assert REASON_TO_ERROR_CODE["too_large"] == "content_too_large"
    assert REASON_TO_ERROR_CODE["encrypted_pdf"] == "forbidden"
    assert RETRY_POLICY["network_timeout"]["max_retries"] == 3
    assert MAX_DELAY_MS == 300000


# ── schedule_retry_or_fail (DB 영속화) ───────────────────────────

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


async def _make_running_job(session, *, attempt_count=0) -> CrawlJob:
    sid = uuid.uuid4()
    session.add(WebSource(id=sid, url=f"https://ex.com/{sid}", domain="ex.com", source_type="article",
                          crawl_status="discovered", license_status="unknown"))
    await session.flush()
    job = CrawlJob(id=uuid.uuid4(), source_id=sid, url="https://ex.com/x", job_type="fetch_html",
                   status=CrawlJobStatus.queued.value)
    session.add(job)
    await session.flush()
    await mark_running(session, job)  # queued → running
    if attempt_count:
        job.attempt_count = attempt_count
        await session.flush()
    return job


def test_schedule_retry_requeues(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_running_job(session)
            out = await schedule_retry_or_fail(session, job, error_code="network_timeout", error_message="timeout")
            await session.commit()
            assert out["action"] == "retry_scheduled"
            assert out["delay_ms"] == 1000
            assert job.status == "queued"          # 재큐됨
            assert job.attempt_count == 1          # +1
            assert job.scheduled_at is not None    # 백오프 예약
            assert job.error_code == "network_timeout"
    asyncio.run(_t())


def test_schedule_terminal_when_exhausted(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_running_job(session, attempt_count=3)  # network 최대 3 소진
            out = await schedule_retry_or_fail(session, job, error_code="network_timeout")
            await session.commit()
            assert out["action"] == "terminal"
            assert out["terminal_reason"] == "network_exhausted"
            assert job.status == "failed_terminal"
            assert job.finished_at is not None
    asyncio.run(_t())


def test_schedule_terminal_for_zero_retry_error(session_factory):
    async def _t():
        async with session_factory() as session:
            job = await _make_running_job(session)
            out = await schedule_retry_or_fail(session, job, error_code="forbidden")
            await session.commit()
            assert out["action"] == "terminal"
            assert out["terminal_reason"] == "forbidden"
            assert job.status == "failed_terminal"
    asyncio.run(_t())