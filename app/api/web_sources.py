"""Web Source API 엔드포인트 본체 (설계서 §6.1/6.2/6.5).

Sprint 0 정책-우선 파이프라인:
  register  → 등록(canonicalize + upsert) → policy_pending
  policy-check → robots + license auto-classify → 전이(allowed_crawl/metadata_only/
                 manual_review_required/blocked_*)
  delete-request → web_delete_requests insert + delete_requested 전이

모든 action 은 web_audit_logs 에 기록.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.enums import BlockedReason, LicenseStatus, SourceStatus
from app.models.schemas import (
    DeleteRequest,
    DeleteRequestResponse,
    PolicyCheckResponse,
    SourceRegisterRequest,
    SourceRegisterResponse,
)
from app.models.tables import (
    CrawlPolicy,
    WebAuditLog,
    WebDeleteRequest,
    WebSource,
)
from app.policy import license_state
from app.policy.robots_checker import check_robots
from app.policy.url_canon import canonicalize_url, extract_domain

router = APIRouter()


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------
def _as_uuid(source_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(source_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="invalid source_id (not a UUID)")


def _audit(
    session: AsyncSession,
    *,
    action: str,
    source_id: uuid.UUID | None = None,
    before: dict | None = None,
    after: dict | None = None,
    reason: str | None = None,
    actor_id: str | None = None,
    actor_role: str | None = None,
) -> None:
    """web_audit_logs 기록(세션에 add 만; commit 은 핸들러가 일괄)."""
    session.add(
        WebAuditLog(
            actor_id=actor_id,
            actor_role=actor_role,
            action=action,
            source_id=source_id,
            before_json=before,
            after_json=after,
            reason=reason,
        )
    )


# ---------------------------------------------------------------------------
# §6.1 Source 등록
# ---------------------------------------------------------------------------
@router.post("/web-sources", response_model=SourceRegisterResponse)
async def register_source(
    req: SourceRegisterRequest,
    session: AsyncSession = Depends(get_session),
) -> SourceRegisterResponse:
    """URL 등록 → crawl_status=policy_pending, next_action=policy_check_required.

    중복 URL(ux_web_sources_url)은 기존 레코드를 반환(멱등).
    """
    try:
        canonical = canonicalize_url(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid url: {exc}")
    domain = extract_domain(req.url)
    if not domain:
        raise HTTPException(status_code=400, detail="cannot extract domain from url")

    org_uuid: uuid.UUID | None = None
    if req.org_id:
        try:
            org_uuid = uuid.UUID(str(req.org_id))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="invalid org_id (not a UUID)")

    # 중복 판정: 원본 url(unique) 우선, 없으면 canonical_url 로도 조회.
    existing = (
        await session.execute(select(WebSource).where(WebSource.url == req.url))
    ).scalar_one_or_none()
    if existing is None:
        existing = (
            await session.execute(
                select(WebSource).where(WebSource.canonical_url == canonical)
            )
        ).scalar_one_or_none()

    if existing is not None:
        # 멱등 반환(전이/감사 없음). 기존 상태 그대로.
        return SourceRegisterResponse(
            source_id=str(existing.id),
            crawl_status=existing.crawl_status,
            license_status=existing.license_status,
            next_action=_next_action_for(existing.crawl_status),
        )

    source = WebSource(
        org_id=org_uuid,
        url=req.url,
        canonical_url=canonical,
        domain=domain,
        source_type=req.source_type,
        discovery_method=req.discovery_method,
        crawl_status=SourceStatus.policy_pending.value,
        license_status=LicenseStatus.unknown.value,
        metadata_json={
            "intended_use": req.intended_use,
            "notes": req.notes,
        },
    )
    session.add(source)
    await session.flush()  # source.id 확보

    _audit(
        session,
        action="source.register",
        source_id=source.id,
        after={
            "url": req.url,
            "canonical_url": canonical,
            "domain": domain,
            "crawl_status": source.crawl_status,
            "license_status": source.license_status,
        },
        reason=req.discovery_method,
    )
    await session.commit()

    return SourceRegisterResponse(
        source_id=str(source.id),
        crawl_status=source.crawl_status,
        license_status=source.license_status,
        next_action="policy_check_required",
    )


def _next_action_for(crawl_status: str) -> str:
    if crawl_status == SourceStatus.policy_pending.value:
        return "policy_check_required"
    if crawl_status == SourceStatus.allowed_crawl.value:
        return "ready_to_crawl"
    if crawl_status == SourceStatus.allowed_metadata_only.value:
        return "metadata_only"
    if crawl_status == "manual_review_required":
        return "manual_review_required"
    if crawl_status == SourceStatus.blocked.value:
        return "blocked"
    if crawl_status == SourceStatus.delete_requested.value:
        return "delete_pending"
    return "policy_check_required"


# ---------------------------------------------------------------------------
# §6.2 Policy Check
# ---------------------------------------------------------------------------
@router.post("/web-sources/{source_id}/policy-check", response_model=PolicyCheckResponse)
async def policy_check(
    source_id: str,
    session: AsyncSession = Depends(get_session),
) -> PolicyCheckResponse:
    """robots/license 정책 게이트 실행 → crawl_status 전이 + allowed_actions 산출."""
    sid = _as_uuid(source_id)
    source = (
        await session.execute(select(WebSource).where(WebSource.id == sid))
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")

    before = {
        "crawl_status": source.crawl_status,
        "license_status": source.license_status,
        "robots_allowed": source.robots_allowed,
    }

    # 1) robots 판정 — canonical path 기준
    path = "/"
    try:
        from urllib.parse import urlsplit

        path = urlsplit(source.canonical_url or source.url).path or "/"
    except Exception:  # noqa: BLE001
        path = "/"
    robots = check_robots(source.domain, path)

    # 2) license 자동 1차 분류 — metadata_json 단서 활용(Sprint 0 엔 등록 시 입력분)
    meta_clues = source.metadata_json if isinstance(source.metadata_json, dict) else {}
    license_result = license_state.auto_classify(meta_clues)

    # 3) terms_review_status — Sprint 0 자동검토 미수행 → 기존 유지/not_reviewed
    terms_review_status = source.terms_review_status or "not_reviewed"

    # 4) 전이 결정
    new_crawl_status: str
    allowed_actions: list[str]
    block_reason: str | None = None

    if robots.robots_allowed is False:
        new_crawl_status = SourceStatus.blocked.value
        allowed_actions = []
        block_reason = BlockedReason.by_robots.value
    elif license_result == LicenseStatus.blocked:
        new_crawl_status = SourceStatus.blocked.value
        allowed_actions = []
        block_reason = BlockedReason.license.value
    elif robots.robots_allowed is True and license_result == LicenseStatus.allowed:
        new_crawl_status = SourceStatus.allowed_crawl.value
        allowed_actions = ["crawl"]
    elif robots.robots_allowed is True and license_result == LicenseStatus.conditional:
        # robots OK 이나 라이선스 조건부 → 메타데이터만 허용(보수적)
        new_crawl_status = SourceStatus.allowed_metadata_only.value
        allowed_actions = ["metadata_only"]
    else:
        # robots 미확인(None) 또는 라이선스 unknown 등 → 수동 검토
        new_crawl_status = "manual_review_required"
        allowed_actions = []

    # 5) source 갱신
    source.crawl_status = new_crawl_status
    source.license_status = license_result.value
    source.robots_allowed = robots.robots_allowed
    source.terms_review_status = terms_review_status

    # 6) crawl_policies upsert(robots 결과 저장)
    policy = (
        await session.execute(
            select(CrawlPolicy).where(CrawlPolicy.domain == source.domain)
        )
    ).scalar_one_or_none()
    allow_crawl = new_crawl_status == SourceStatus.allowed_crawl.value
    if policy is None:
        policy = CrawlPolicy(
            domain=source.domain,
            robots_url=f"https://{source.domain}/robots.txt",
            allow_crawl=allow_crawl,
            crawl_delay_ms=robots.crawl_delay_ms or 1000,
            review_status="auto_checked" if robots.checked else "needs_review",
            include_patterns=[],
            exclude_patterns=[],
        )
        if robots.sitemaps:
            policy.include_patterns = list(robots.sitemaps)
        session.add(policy)
    else:
        policy.allow_crawl = allow_crawl
        policy.robots_url = f"https://{source.domain}/robots.txt"
        if robots.crawl_delay_ms is not None:
            policy.crawl_delay_ms = robots.crawl_delay_ms
        policy.review_status = "auto_checked" if robots.checked else "needs_review"
        if robots.sitemaps:
            policy.include_patterns = list(robots.sitemaps)
    from sqlalchemy import func as _sqlfunc

    policy.robots_checked_at = _sqlfunc.now()

    # 7) audit
    _audit(
        session,
        action="source.policy_check",
        source_id=source.id,
        before=before,
        after={
            "crawl_status": new_crawl_status,
            "license_status": license_result.value,
            "robots_allowed": robots.robots_allowed,
            "robots_checked": robots.checked,
            "robots_note": robots.note,
            "sitemaps": robots.sitemaps,
        },
        reason=block_reason,
    )
    await session.commit()

    return PolicyCheckResponse(
        source_id=str(source.id),
        robots_allowed=robots.robots_allowed,
        terms_review_status=terms_review_status,
        license_status=license_result.value,
        crawl_status=new_crawl_status,
        allowed_actions=allowed_actions,
    )


# ---------------------------------------------------------------------------
# §6.5 Delete Request
# ---------------------------------------------------------------------------
@router.post("/web-sources/{source_id}/delete-request", response_model=DeleteRequestResponse)
async def delete_request(
    source_id: str,
    req: DeleteRequest,
    session: AsyncSession = Depends(get_session),
) -> DeleteRequestResponse:
    """삭제 요청 → web_delete_requests insert + source delete_requested 전이.

    Sprint 0 엔 pattern/snapshot 이 없어 전파는 TODO.
    """
    sid = _as_uuid(source_id)
    source = (
        await session.execute(select(WebSource).where(WebSource.id == sid))
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")

    before = {"crawl_status": source.crawl_status}

    dr = WebDeleteRequest(
        source_id=source.id,
        requester=req.requester,
        requester_contact=req.requester_contact,
        request_type=req.request_type,
        reason=req.reason,
        status="received",
    )
    session.add(dr)

    source.crawl_status = SourceStatus.delete_requested.value

    # TODO(sprint1+): 전파 — crawl_snapshots access 차단, web_patterns.pattern_status=blocked,
    #                 embedding 비활성화. Sprint 0 엔 해당 레코드가 없어 source 전이만.

    _audit(
        session,
        action="source.delete_request",
        source_id=source.id,
        before=before,
        after={"crawl_status": source.crawl_status},
        reason=req.reason,
        actor_id=req.requester,
    )
    await session.commit()

    return DeleteRequestResponse(
        source_id=str(source.id),
        crawl_status=source.crawl_status,
        status="received",
    )
