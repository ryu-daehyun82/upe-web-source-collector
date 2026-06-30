"""운영 API 라우트 통합테스트 — sqlite 인메모리 + FastAPI dependency override."""
import asyncio
import uuid

import pytest

pytest.importorskip("aiosqlite")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.tables import Base, WebSource, WebPattern  # noqa: E402


@pytest.fixture()
def client():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    async def _create() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())

    async def _override():
        async with factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        c._factory = factory  # 테스트에서 직접 시드 주입용
        yield c
    app.dependency_overrides.clear()


def _seed_source(factory) -> str:
    sid = uuid.uuid4()

    async def _ins():
        async with factory() as session:
            session.add(WebSource(
                id=sid, url=f"https://ex.com/{sid}", domain="ex.com", source_type="article",
                crawl_status="crawled", license_status="allowed",
            ))
            await session.commit()

    asyncio.run(_ins())
    return str(sid)


def _seed_pattern(factory, source_id: str) -> str:
    pid = uuid.uuid4()

    async def _ins():
        async with factory() as session:
            session.add(WebPattern(
                id=pid, source_id=uuid.UUID(source_id), pattern_type="html_layout",
                abstraction_level="structural", original_reuse_risk="low", feature_json={},
                license_status="allowed", pii_status="clean", pattern_status="reuse_risk_scored",
                embedding="vec",
            ))
            await session.commit()

    asyncio.run(_ins())
    return str(pid)


def test_create_crawl_job(client):
    sid = _seed_source(client._factory)
    r = client.post("/api/v1/crawl-jobs", json={"source_id": sid, "job_type": "fetch_html"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    assert body["status"] == "queued"
    # 멱등: 동일 (source, type, hash) 재호출 → created False
    r2 = client.post("/api/v1/crawl-jobs", json={"source_id": sid, "job_type": "fetch_html"})
    assert r2.json()["created"] is False
    assert r2.json()["job_id"] == body["job_id"]


def test_create_crawl_job_source_404(client):
    r = client.post("/api/v1/crawl-jobs", json={"source_id": str(uuid.uuid4()), "job_type": "fetch_html"})
    assert r.status_code == 404


def test_approve_pattern(client):
    sid = _seed_source(client._factory)
    pid = _seed_pattern(client._factory, sid)
    r = client.post(f"/api/v1/web-patterns/{pid}/approve", json={"reviewer_id": "op1", "reason": "ok"})
    assert r.status_code == 200, r.text
    assert r.json()["pattern_status"] == "approved"


def test_block_pattern(client):
    sid = _seed_source(client._factory)
    pid = _seed_pattern(client._factory, sid)
    r = client.post(f"/api/v1/web-patterns/{pid}/block", json={"reviewer_id": "op1", "reason": "brand risk"})
    assert r.status_code == 200
    assert r.json()["pattern_status"] == "blocked"


def test_pattern_404(client):
    r = client.post(f"/api/v1/web-patterns/{uuid.uuid4()}/approve", json={"reviewer_id": "op1"})
    assert r.status_code == 404


def test_apply_delete(client):
    sid = _seed_source(client._factory)
    _seed_pattern(client._factory, sid)
    r = client.post(f"/api/v1/web-sources/{sid}/apply-delete", json={"actor_id": "admin"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["patterns_blocked"] == 1
    assert body["status"] == "delete_requested"


def test_recheck_hold(client):
    sid = _seed_source(client._factory)
    _seed_pattern(client._factory, sid)
    r = client.post(f"/api/v1/web-sources/{sid}/recheck", json={"result": "license_changed"})
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "hold"
    assert r.json()["patterns_blocked"] == 1


def test_recheck_unknown_400(client):
    sid = _seed_source(client._factory)
    r = client.post(f"/api/v1/web-sources/{sid}/recheck", json={"result": "??bogus"})
    assert r.status_code == 400


def test_recheck_source_404(client):
    r = client.post(f"/api/v1/web-sources/{uuid.uuid4()}/recheck", json={"result": "unchanged"})
    assert r.status_code == 404