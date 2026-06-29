import asyncio
import uuid

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.tables import Base, WebSource
from app.pattern_build import persist_pattern, build_and_persist_pattern, get_pattern
from app.pipeline import run_pattern_governance
from app.models.enums import PatternStatus, ReuseRisk


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


def test_persist_maps_decision_fields(session_factory):
    async def _test():
        async with session_factory() as session:
            sid = await _make_source(session)
            raw = {"layout_similarity": 0.1, "color_signature": 0.1, "structure_fingerprint": 0.1}
            decision = run_pattern_governance(raw)
            pat = await persist_pattern(
                session, source_id=sid, decision=decision,
                pattern_type="slide_layout", license_status="allowed",
            )
            await session.commit()
            assert pat.id is not None
            assert pat.original_reuse_risk == decision.original_reuse_risk
            assert float(pat.reuse_score) == decision.reuse_score
            assert pat.recon_test_passed == decision.recon_test_passed
            assert pat.pattern_status == decision.pattern_status
            assert pat.pii_status == decision.pii_status
            assert pat.feature_json == decision.abstracted_feature
            assert pat.reuse_subscores == decision.reuse_subscores
            assert pat.license_status == "allowed"
            assert pat.pattern_type == "slide_layout"
    asyncio.run(_test())


def test_build_and_persist_clean_approved(session_factory):
    async def _test():
        async with session_factory() as session:
            sid = await _make_source(session)
            raw = {"layout_similarity": 0.1, "color_signature": 0.1, "structure_fingerprint": 0.1}
            pat, decision = await build_and_persist_pattern(
                session, source_id=sid, raw_feature=raw,
                pattern_type="layout", license_status="allowed",
            )
            await session.commit()
            assert decision.operational is True
            assert pat.pattern_status == PatternStatus.approved.value
            assert pat.original_reuse_risk == ReuseRisk.low.value
    asyncio.run(_test())


def test_build_and_persist_text_leak_blocked(session_factory):
    async def _test():
        async with session_factory() as session:
            sid = await _make_source(session)
            txt = "exact original sentence leaking fully into the pattern text here right now"
            raw = {"original_text": txt, "pattern_text": txt}
            pat, decision = await build_and_persist_pattern(
                session, source_id=sid, raw_feature=raw,
                pattern_type="text", license_status="allowed",
            )
            await session.commit()
            assert decision.operational is False
            assert pat.pattern_status == PatternStatus.blocked.value
            assert pat.original_reuse_risk == ReuseRisk.blocked.value
    asyncio.run(_test())


def test_get_pattern_roundtrip(session_factory):
    async def _test():
        async with session_factory() as session:
            sid = await _make_source(session)
            raw = {"layout_similarity": 0.1}
            pat, _ = await build_and_persist_pattern(
                session, source_id=sid, raw_feature=raw,
                pattern_type="layout", license_status="allowed",
            )
            await session.commit()
            found = await get_pattern(session, pat.id)
            assert found is not None and found.id == pat.id
            missing = await get_pattern(session, uuid.uuid4())
            assert missing is None
    asyncio.run(_test())


def test_org_id_str_persisted(session_factory):
    async def _test():
        async with session_factory() as session:
            sid = await _make_source(session)
            org = uuid.uuid4()
            raw = {"layout_similarity": 0.1}
            decision = run_pattern_governance(raw)
            pat = await persist_pattern(
                session, source_id=sid, decision=decision,
                pattern_type="layout", license_status="allowed", org_id=str(org),
            )
            await session.commit()
            assert pat.org_id == org
    asyncio.run(_test())


def test_feature_json_is_abstracted_not_raw(session_factory):
    async def _test():
        async with session_factory() as session:
            sid = await _make_source(session)
            raw = {"layout_similarity": 0.1, "raw_text": "original body", "section_order": [1, 2]}
            pat, decision = await build_and_persist_pattern(
                session, source_id=sid, raw_feature=raw,
                pattern_type="layout", license_status="allowed",
            )
            await session.commit()
            assert "raw_text" not in pat.feature_json
            assert "section_order" in pat.feature_json
    asyncio.run(_test())