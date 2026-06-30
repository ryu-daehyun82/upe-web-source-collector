import asyncio
import uuid

import pytest

from app.rbac import (
    Role, Permission, Principal, ROLE_PERMISSIONS,
    parse_roles, has_permission,
)


# ── 모듈 단위(순수) ───────────────────────────────────────────────

def test_parse_roles():
    assert parse_roles("admin") == frozenset({Role.admin})
    assert parse_roles("pattern_reviewer, auditor") == frozenset({Role.pattern_reviewer, Role.auditor})
    assert parse_roles(None) == frozenset()
    assert parse_roles("") == frozenset()
    assert parse_roles("bogus,admin") == frozenset({Role.admin})  # 미지 무시


def test_admin_has_all():
    p = Principal("a", frozenset({Role.admin}))
    for perm in Permission:
        assert p.has(perm) is True


def test_pattern_reviewer_scope():
    p = Principal(None, frozenset({Role.pattern_reviewer}))
    assert p.has(Permission.pattern_approve) is True
    assert p.has(Permission.pattern_block) is True
    assert p.has(Permission.pattern_view_feature) is True
    assert p.has(Permission.delete_apply) is False
    assert p.has(Permission.crawl_job_create) is False


def test_privacy_reviewer_delete():
    p = Principal(None, frozenset({Role.privacy_reviewer}))
    assert p.has(Permission.delete_apply) is True
    assert p.has(Permission.delete_request) is True
    assert p.has(Permission.pattern_approve) is False


def test_auditor_scope():
    p = Principal(None, frozenset({Role.auditor}))
    assert p.has(Permission.audit_view) is True
    assert p.has(Permission.crawl_job_create) is False
    assert p.has(Permission.snapshot_access) is False


def test_source_operator_scope():
    p = Principal(None, frozenset({Role.source_operator}))
    assert p.has(Permission.crawl_job_create) is True
    assert p.has(Permission.recheck) is True
    assert p.has(Permission.pattern_approve) is False


def test_snapshot_access_admin_only():
    assert Principal(None, frozenset({Role.admin})).has(Permission.snapshot_access) is True
    for r in (Role.source_operator, Role.license_reviewer, Role.privacy_reviewer,
              Role.pattern_reviewer, Role.auditor):
        assert Principal(None, frozenset({r})).has(Permission.snapshot_access) is False


def test_empty_principal_denied():
    p = Principal(None, frozenset())
    assert p.has(Permission.crawl_job_create) is False
    assert p.has(Permission.audit_view) is False


def test_has_permission_helper():
    assert has_permission([Role.source_operator], Permission.crawl_job_create) is True
    assert has_permission([Role.auditor], Permission.crawl_job_create) is False
    assert has_permission([], Permission.recheck) is False


def test_multi_role_union():
    p = Principal(None, frozenset({Role.auditor, Role.source_operator}))
    assert p.has(Permission.audit_view) is True        # auditor
    assert p.has(Permission.crawl_job_create) is True  # source_operator


# ── FastAPI 강제(통합) ───────────────────────────────────────────

pytest.importorskip("aiosqlite")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.tables import Base, WebSource  # noqa: E402


@pytest.fixture()
def client():
    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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
        c._factory = factory
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


def test_route_403_without_role(client):
    sid = _seed_source(client._factory)
    # 헤더 없음 → 권한 없음 → 403
    r = client.post("/api/v1/crawl-jobs", json={"source_id": sid, "job_type": "fetch_html"})
    assert r.status_code == 403
    assert "crawl_job_create" in r.json()["detail"]


def test_route_403_wrong_role(client):
    sid = _seed_source(client._factory)
    # auditor는 crawl_job_create 권한 없음 → 403
    r = client.post(
        "/api/v1/crawl-jobs", json={"source_id": sid, "job_type": "fetch_html"},
        headers={"X-Upe-Roles": "auditor"},
    )
    assert r.status_code == 403


def test_route_200_correct_role(client):
    sid = _seed_source(client._factory)
    # source_operator는 crawl_job_create 보유 → 200
    r = client.post(
        "/api/v1/crawl-jobs", json={"source_id": sid, "job_type": "fetch_html"},
        headers={"X-Upe-Roles": "source_operator", "X-Upe-Actor": "op-1"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["created"] is True


def test_approve_403_for_source_operator(client):
    # source_operator는 pattern_approve 권한 없음 → 403 (pattern 없어도 권한체크 먼저)
    r = client.post(
        f"/api/v1/web-patterns/{uuid.uuid4()}/approve", json={"reviewer_id": "x"},
        headers={"X-Upe-Roles": "source_operator"},
    )
    assert r.status_code == 403


def test_approve_200_for_pattern_reviewer_then_404(client):
    # pattern_reviewer는 권한 보유 → 권한 통과 후 패턴 없으면 404
    r = client.post(
        f"/api/v1/web-patterns/{uuid.uuid4()}/approve", json={"reviewer_id": "rev"},
        headers={"X-Upe-Roles": "pattern_reviewer"},
    )
    assert r.status_code == 404
