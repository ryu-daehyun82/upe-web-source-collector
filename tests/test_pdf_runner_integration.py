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
from app.workers.pdf_worker import PDFParseWorker
from app.workers.http_worker import FetchResult


class _FakeFetcher:
    def __init__(self, *, content=b"%PDF-1.4 /Type /Page /Type /Page /Font %%EOF",
                 content_type="application/pdf", raise_exc=None):
        self.content = content
        self.content_type = content_type
        self.raise_exc = raise_exc

    def fetch(self, url, *, max_bytes=52428800, timeout=30.0):
        if self.raise_exc is not None:
            raise self.raise_exc
        c = self.content[:max_bytes]
        return FetchResult(url=url, status_code=200, content=c, content_type=self.content_type,
                           content_hash=hashlib.sha256(c).hexdigest(), byte_size=len(c),
                           truncated=len(self.content) > max_bytes)


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
        id=sid, url=f"https://ex.com/{sid}.pdf", domain="ex.com",
        source_type="pdf", crawl_status="discovered", license_status="unknown",
    ))
    await session.flush()
    return sid


def test_pdf_through_runner_persists(session_factory):
    async def _test():
        async with session_factory() as session:
            sid = await _make_source(session)
            worker = PDFParseWorker(fetcher=_FakeFetcher(
                content=b"%PDF-1.4 /Type /Page /Type /Page /Font %%EOF"))
            job = {"url": "https://ex.com/doc.pdf", "job_type": "parse_pdf"}
            out = await process_crawl_job(
                session, job=job, worker=worker, source_id=sid,
                license_status="allowed", pattern_type="pdf_layout",
            )
            await session.commit()
            assert out["ok"] is True
            assert out["stage"] == "persisted"
            assert out["operational"] is True
            rows = (await session.execute(select(WebPattern))).scalars().all()
            assert len(rows) == 1
            assert rows[0].pattern_type == "pdf_layout"

    asyncio.run(_test())


def test_encrypted_pdf_blocked_at_postcheck(session_factory):
    async def _test():
        async with session_factory() as session:
            sid = await _make_source(session)
            worker = PDFParseWorker(fetcher=_FakeFetcher(
                content=b"%PDF-1.4 /Encrypt 1 0 R /Type /Page"))
            job = {"url": "https://ex.com/enc.pdf", "job_type": "parse_pdf"}
            out = await process_crawl_job(
                session, job=job, worker=worker, source_id=sid,
                license_status="allowed", pattern_type="pdf_layout",
            )
            await session.commit()
            assert out["ok"] is False
            assert out["stage"] == "postcheck"
            rows = (await session.execute(select(WebPattern))).scalars().all()
            assert len(rows) == 0

    asyncio.run(_test())