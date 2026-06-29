import asyncio
import hashlib
import uuid

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.tables import Base, WebSource, WebPattern
from app.workers.runner import process_crawl_job
from app.workers.http_worker import HTTPFetchWorker, FetchResult


class _FakeFetcher:
    def __init__(self, *, content=b"<html><main><section><p>hello</p></section></main></html>",
                 content_type="text/html", status_code=200, raise_exc=None):
        self.content = content
        self.content_type = content_type
        self.status_code = status_code
        self.raise_exc = raise_exc

    def fetch(self, url, *, max_bytes=52428800, timeout=30.0):
        if self.raise_exc is not None:
            raise self.raise_exc
        content = self.content[:max_bytes]
        return FetchResult(url=url, status_code=self.status_code, content=content,
                           content_type=self.content_type,
                           content_hash=hashlib.sha256(content).hexdigest(),
                           byte_size=len(content), truncated=len(self.content) > max_bytes)


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


def test_full_chain_persists_pattern(session_factory):
    async def _test():
        async with session_factory() as session:
            sid = await _make_source(session)
            worker = HTTPFetchWorker(fetcher=_FakeFetcher())
            job = {"url": "https://ex.com/page", "job_type": "fetch_html"}
            out = await process_crawl_job(session, job=job, worker=worker, source_id=sid, license_status="allowed")
            await session.commit()
            assert out["ok"] is True
            assert out["stage"] == "persisted"
            assert out["pattern_id"] is not None
            assert out["operational"] is True
            assert out["pattern_status"] == "approved"
            rows = (await session.execute(select(WebPattern))).scalars().all()
            assert len(rows) == 1
            assert str(rows[0].id) == out["pattern_id"]
            assert "original_text" not in rows[0].feature_json

    asyncio.run(_test())


def test_precheck_failure_no_persist(session_factory):
    async def _test():
        async with session_factory() as session:
            sid = await _make_source(session)
            worker = HTTPFetchWorker(fetcher=_FakeFetcher())
            job = {"job_type": "fetch_html"}
            out = await process_crawl_job(session, job=job, worker=worker, source_id=sid, license_status="allowed")
            await session.commit()
            assert out["ok"] is False
            assert out["stage"] == "precheck"
            assert out["pattern_id"] is None
            rows = (await session.execute(select(WebPattern))).scalars().all()
            assert len(rows) == 0

    asyncio.run(_test())


def test_postcheck_failure_too_large_no_persist(session_factory):
    async def _test():
        async with session_factory() as session:
            sid = await _make_source(session)
            worker = HTTPFetchWorker(fetcher=_FakeFetcher(content=b"<html>" + b"x" * 100 + b"</html>"), max_bytes=10)
            job = {"url": "https://ex.com/big", "job_type": "fetch_html", "job_config": {"max_bytes": 1000}}
            out = await process_crawl_job(session, job=job, worker=worker, source_id=sid, license_status="allowed")
            await session.commit()
            assert out["ok"] is False
            assert out["stage"] == "postcheck"
            rows = (await session.execute(select(WebPattern))).scalars().all()
            assert len(rows) == 0

    asyncio.run(_test())


def test_fetch_exception_postcheck_failed(session_factory):
    async def _test():
        async with session_factory() as session:
            sid = await _make_source(session)
            worker = HTTPFetchWorker(fetcher=_FakeFetcher(raise_exc=RuntimeError("net down")))
            job = {"url": "https://ex.com/x", "job_type": "fetch_html"}
            out = await process_crawl_job(session, job=job, worker=worker, source_id=sid, license_status="allowed")
            await session.commit()
            assert out["ok"] is False
            assert out["stage"] == "postcheck"
            assert out["reason"] == "fetch_failed"

    asyncio.run(_test())