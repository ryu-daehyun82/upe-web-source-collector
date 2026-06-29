"""brand_risk DB 어댑터 테스트 — sqlite+aiosqlite 인메모리(StaticPool).

pytest-asyncio 미사용 — 기존 test_handlers_integration.py 패턴대로 동기 픽스처에서
asyncio.run 으로 스키마 생성, 각 테스트는 asyncio.run 으로 async 본문 실행.
"""
import asyncio

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.models.tables import Base  # noqa: E402
from app.policy.brand_risk_lookup import (  # noqa: E402
    load_brand_risk_map,
    make_brand_risk_lookup,
    upsert_brand_risk,
)


@pytest.fixture()
def session_factory():
    # 단일 공유 인메모리 sqlite (StaticPool 로 커넥션 공유 → 스키마 유지)
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async def _create() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    asyncio.run(engine.dispose())


def test_upsert_insert_then_load(session_factory):
    async def run():
        async with session_factory() as session:
            await upsert_brand_risk(session, "a", 0.9)
            await upsert_brand_risk(session, "b", 0.1)
            await session.commit()
            result = await load_brand_risk_map(session)
            assert result == {"a": 0.9, "b": 0.1}

    asyncio.run(run())


def test_upsert_update_existing(session_factory):
    async def run():
        async with session_factory() as session:
            await upsert_brand_risk(session, "x", 0.3)
            await session.commit()
            await upsert_brand_risk(session, "x", 0.8)
            await session.commit()
            result = await load_brand_risk_map(session)
            assert result == {"x": 0.8}

    asyncio.run(run())


def test_upsert_clamps(session_factory):
    async def run():
        async with session_factory() as session:
            await upsert_brand_risk(session, "high", 1.5)
            await upsert_brand_risk(session, "low", -0.2)
            await session.commit()
            result = await load_brand_risk_map(session)
            assert result == {"high": 1.0, "low": 0.0}

    asyncio.run(run())


def test_load_subset(session_factory):
    async def run():
        async with session_factory() as session:
            await upsert_brand_risk(session, "a", 0.5)
            await upsert_brand_risk(session, "b", 0.6)
            await upsert_brand_risk(session, "c", 0.7)
            await session.commit()
            result = await load_brand_risk_map(session, ["a", "c"])
            assert result == {"a": 0.5, "c": 0.7}

    asyncio.run(run())


def test_load_empty_domains_returns_empty(session_factory):
    async def run():
        async with session_factory() as session:
            await upsert_brand_risk(session, "a", 0.5)
            await session.commit()
            result = await load_brand_risk_map(session, [])
            assert result == {}

    asyncio.run(run())


def test_make_lookup_hit_and_miss():
    cache = {"x": 0.7}
    lookup = make_brand_risk_lookup(cache)
    assert lookup("x") == 0.7
    assert lookup("none") is None


def test_lookup_integrates_with_reuse_risk(session_factory):
    from app.pattern.reuse_risk import compute_reuse_risk

    async def run():
        async with session_factory() as session:
            await upsert_brand_risk(session, "brandy.com", 0.9)
            await session.commit()
            cache = await load_brand_risk_map(session)
            lookup = make_brand_risk_lookup(cache)
            feat = {"domain": "brandy.com"}
            out = compute_reuse_risk(feat, brand_risk_lookup=lookup)
            # 테이블 brand_risk 값이 sub-score 로 반영됐는지
            assert out["subscores"]["brand_risk"] == 0.9

    asyncio.run(run())