import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.tables import Base, WebSource, WebPattern, WebDeleteRequest, WebAuditLog, CrawlSnapshot
from app.events import InMemoryEventPublisher, TOPIC_DELETE_REQUESTED, TOPIC_DELETE_COMPLETED
from app.delete_recheck import (
    request_delete, apply_delete, is_recheck_due, apply_recheck_result,
    RECHECK_INTERVAL_DAYS,
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


async def _make_source(session, *, crawl_status="crawled", last_checked_at=None) -> uuid.UUID:
    sid = uuid.uuid4()
    session.add(WebSource(
        id=sid, url=f"https://ex.com/{sid}", domain="ex.com", source_type="article",
        crawl_status=crawl_status, license_status="allowed", last_checked_at=last_checked_at,
    ))
    await session.flush()
    return sid


async def _make_pattern(session, source_id, *, embedding="vec") -> uuid.UUID:
    pid = uuid.uuid4()
    session.add(WebPattern(
        id=pid, source_id=source_id, pattern_type="html_layout", abstraction_level="structural",
        original_reuse_risk="low", feature_json={}, license_status="allowed", pii_status="clean",
        pattern_status="approved", embedding=embedding,
    ))
    await session.flush()
    return pid


# ── 재검증 주기(순수) ──────────────────────────────────────────────

def test_is_recheck_due_never_checked():
    assert is_recheck_due(None, "general") is True


def test_is_recheck_due_recent_false():
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert is_recheck_due(now, "general", now=now) is False


def test_is_recheck_due_overdue_general():
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    last = now - timedelta(days=40)  # general 주기 30일 초과
    assert is_recheck_due(last, "general", now=now) is True


def test_is_recheck_due_high_risk_tight():
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    last = now - timedelta(days=8)  # high_risk 주기 7일 초과
    assert is_recheck_due(last, "high_risk", now=now) is True
    assert is_recheck_due(now - timedelta(days=5), "high_risk", now=now) is False


def test_is_recheck_due_unknown_category_uses_general():
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    last = now - timedelta(days=40)
    assert is_recheck_due(last, "weird", now=now) is True
    assert RECHECK_INTERVAL_DAYS["general"] == 30


def test_is_recheck_due_naive_last_checked():
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    last_naive = datetime(2026, 1, 1)  # tz-naive → utc 가정
    assert is_recheck_due(last_naive, "general", now=now) is True


# ── 삭제 워크플로우 ────────────────────────────────────────────────

def test_request_delete_received(session_factory):
    async def _t():
        async with session_factory() as session:
            sid = await _make_source(session)
            dr = await request_delete(session, source_id=sid, request_type="rights_holder_request",
                                      requester="r", reason="please delete")
            await session.commit()
            assert dr.status == "received"
            assert dr.request_type == "rights_holder_request"
    asyncio.run(_t())


def test_apply_delete_propagates(session_factory):
    async def _t():
        async with session_factory() as session:
            sid = await _make_source(session)
            await _make_pattern(session, sid)
            await _make_pattern(session, sid)
            dr = await request_delete(session, source_id=sid, request_type="gdpr")
            pub = InMemoryEventPublisher()
            out = await apply_delete(session, source_id=sid, request_id=dr.id, publisher=pub, trace_id="T9")
            await session.commit()

            assert out["patterns_blocked"] == 2
            assert out["status"] == "delete_requested"
            # 패턴 전부 blocked + embedding None
            pats = (await session.execute(select(WebPattern).where(WebPattern.source_id == sid))).scalars().all()
            assert all(p.pattern_status == "blocked" for p in pats)
            assert all(p.embedding is None for p in pats)
            # source 상태
            src = await session.get(WebSource, sid)
            assert src.crawl_status == "delete_requested"
            # 삭제요청 resolved
            dr2 = await session.get(WebDeleteRequest, dr.id)
            assert dr2.status == "resolved" and dr2.resolved_at is not None
            # 감사로그
            audits = (await session.execute(select(WebAuditLog).where(WebAuditLog.action == "delete_propagated"))).scalars().all()
            assert len(audits) == 1
            # 이벤트
            assert len(pub.by_type(TOPIC_DELETE_REQUESTED)) == 1
            assert len(pub.by_type(TOPIC_DELETE_COMPLETED)) == 1
            assert pub.by_type(TOPIC_DELETE_REQUESTED)[0]["trace_id"] == "T9"
    asyncio.run(_t())


def test_apply_delete_restricts_snapshots(session_factory):
    async def _t():
        async with session_factory() as session:
            sid = await _make_source(session)
            session.add(CrawlSnapshot(id=uuid.uuid4(), source_id=sid, snapshot_type="raw",
                                      storage_ref="s3://x", content_hash="h", access_level="restricted"))
            await session.flush()
            out = await apply_delete(session, source_id=sid)
            await session.commit()
            assert out["snapshots_blocked"] == 1
            snap = (await session.execute(select(CrawlSnapshot).where(CrawlSnapshot.source_id == sid))).scalar_one()
            assert snap.access_level == "blocked"
    asyncio.run(_t())


# ── 재검증 결과 처리 ──────────────────────────────────────────────

def test_recheck_unchanged(session_factory):
    async def _t():
        async with session_factory() as session:
            sid = await _make_source(session)
            await _make_pattern(session, sid)
            out = await apply_recheck_result(session, source_id=sid, result="unchanged")
            await session.commit()
            assert out["action"] == "none"
            assert out["patterns_blocked"] == 0
            src = await session.get(WebSource, sid)
            assert src.last_checked_at is not None
            pat = (await session.execute(select(WebPattern).where(WebPattern.source_id == sid))).scalar_one()
            assert pat.pattern_status == "approved"  # 안 막힘
    asyncio.run(_t())


def test_recheck_hold_blocks(session_factory):
    async def _t():
        async with session_factory() as session:
            sid = await _make_source(session)
            await _make_pattern(session, sid)
            out = await apply_recheck_result(session, source_id=sid, result="license_changed")
            await session.commit()
            assert out["action"] == "hold"
            assert out["patterns_blocked"] == 1
            pat = (await session.execute(select(WebPattern).where(WebPattern.source_id == sid))).scalar_one()
            assert pat.pattern_status == "blocked"
    asyncio.run(_t())


def test_recheck_removed(session_factory):
    async def _t():
        async with session_factory() as session:
            sid = await _make_source(session)
            await _make_pattern(session, sid)
            out = await apply_recheck_result(session, source_id=sid, result="content_removed")
            await session.commit()
            assert out["action"] == "removed"
            assert out["patterns_blocked"] == 1
            src = await session.get(WebSource, sid)
            assert src.crawl_status == "blocked"
    asyncio.run(_t())


def test_recheck_unknown_raises(session_factory):
    async def _t():
        async with session_factory() as session:
            sid = await _make_source(session)
            with pytest.raises(ValueError):
                await apply_recheck_result(session, source_id=sid, result="??bogus")
    asyncio.run(_t())