"""수집측 파이프라인 end-to-end 통합테스트 — 실 컴포넌트 조합.

실 Frontier + 실 RedisPolitenessLimiter(fakeredis) + 실 enqueue_crawl_job(sqlite).
robots 만 가짜(allow). 모듈들이 실제로 조합되어 동작하는지 증명.
"""
import asyncio
import uuid

import pytest

pytest.importorskip("aiosqlite")
fakeredis = pytest.importorskip("fakeredis")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.tables import Base, WebSource, CrawlJob
from app.frontier import Frontier, FrontierConfig
from app.crawl_planner import CrawlPlanner
from app.redis_backend import RedisPolitenessLimiter
from app.jobs import enqueue_crawl_job
from app.policy.robots_checker import RobotsDecision


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


def _robots_allow(domain, path, ua):
    return RobotsDecision(
        domain=domain, robots_allowed=True, crawl_delay_ms=None,
        sitemaps=[], checked=True, note="",
    )


def _make_db_enqueue(session):
    async def _enqueue(item):
        sid = uuid.uuid4()
        session.add(WebSource(
            id=sid, url=item.url, domain=item.domain,
            source_type="article", crawl_status="discovered", license_status="unknown",
        ))
        await session.flush()
        job, created = await enqueue_crawl_job(
            session, source_id=sid, url=item.url, job_type="download_file",
        )
        return (str(job.id), created)
    return _enqueue


def test_end_to_end_enqueues_jobs(session_factory):
    async def _run():
        async with session_factory() as session:
            f = Frontier(
                config=FrontierConfig(same_domain_only=True),
                seeds=["https://ex.com/a", "https://ex.com/b"],
            )
            pol = RedisPolitenessLimiter(fakeredis.FakeStrictRedis())
            planner = CrawlPlanner(
                f, robots_check=_robots_allow, politeness=pol,
                enqueue=_make_db_enqueue(session), default_crawl_delay_ms=0,
            )
            outs = await planner.plan_all()
            await session.commit()
            assert [o.decision for o in outs] == ["enqueued", "enqueued"]
            rows = (await session.execute(select(CrawlJob))).scalars().all()
            assert len(rows) == 2
            assert all(j.status == "queued" for j in rows)

    asyncio.run(_run())


def test_politeness_defers_second_same_domain(session_factory):
    async def _run():
        async with session_factory() as session:
            f = Frontier(
                config=FrontierConfig(same_domain_only=True),
                seeds=["https://ex.com/a", "https://ex.com/b"],
            )
            pol = RedisPolitenessLimiter(fakeredis.FakeStrictRedis())
            planner = CrawlPlanner(
                f, robots_check=_robots_allow, politeness=pol,
                enqueue=_make_db_enqueue(session), default_crawl_delay_ms=10000,
            )
            outs = await planner.plan_all()
            await session.commit()
            decisions = [o.decision for o in outs]
            assert decisions.count("enqueued") == 1
            assert decisions.count("deferred_politeness") == 1
            rows = (await session.execute(select(CrawlJob))).scalars().all()
            assert len(rows) == 1

    asyncio.run(_run())


def test_idempotent_same_url_twice(session_factory):
    async def _run():
        async with session_factory() as session:
            f = Frontier(
                config=FrontierConfig(),
                seeds=["https://ex.com/a", "https://ex.com/a"],
            )
            planner = CrawlPlanner(
                f, robots_check=_robots_allow, enqueue=_make_db_enqueue(session),
            )
            outs = await planner.plan_all()
            assert len(outs) == 1
            assert outs[0].decision == "enqueued"

    asyncio.run(_run())