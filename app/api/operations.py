"""운영 API 라우트 (§6.3 Crawl Job / §6.4 Pattern 승인·차단 + 삭제전파/재검증).

기존 서비스 함수(enqueue_crawl_job / apply_delete / apply_recheck_result)를 호출하는
얇은 라우터. 모든 변경은 web_audit_logs 또는 서비스 내부 감사로 기록된다.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.tables import WebSource, WebPattern, WebAuditLog
from app.models.enums import PatternStatus
from app.models.schemas import (
    CrawlJobCreateRequest, CrawlJobCreateResponse,
    PatternReviewRequest, PatternReviewResponse,
    ApplyDeleteRequest, ApplyDeleteResponse,
    RecheckRequest, RecheckResponse,
)
from app.jobs import enqueue_crawl_job
from app.delete_recheck import apply_delete, apply_recheck_result

router = APIRouter()


def _as_uuid(v: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="invalid id (not a UUID)")


@router.post("/crawl-jobs", response_model=CrawlJobCreateResponse)
async def create_crawl_job(
    req: CrawlJobCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> CrawlJobCreateResponse:
    """§6.3 크롤 작업 멱등 생성. source.url 로 enqueue."""
    sid = _as_uuid(req.source_id)
    source = (await session.execute(select(WebSource).where(WebSource.id == sid))).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    job, created = await enqueue_crawl_job(
        session,
        source_id=sid,
        url=source.url,
        job_type=req.job_type,
        content_hash=req.content_hash,
        priority=req.priority,
        job_config=req.job_config,
    )
    await session.commit()
    return CrawlJobCreateResponse(job_id=str(job.id), status=job.status, created=created)


@router.post("/web-patterns/{pattern_id}/approve", response_model=PatternReviewResponse)
async def approve_pattern(
    pattern_id: str,
    req: PatternReviewRequest,
    session: AsyncSession = Depends(get_session),
) -> PatternReviewResponse:
    """§6.4 패턴 승인 → pattern_status=approved + 감사."""
    pid = _as_uuid(pattern_id)
    pat = (await session.execute(select(WebPattern).where(WebPattern.id == pid))).scalar_one_or_none()
    if pat is None:
        raise HTTPException(status_code=404, detail="pattern not found")
    before = pat.pattern_status
    pat.pattern_status = PatternStatus.approved.value
    session.add(WebAuditLog(
        action="pattern.approve", pattern_id=pat.id, source_id=pat.source_id,
        before_json={"pattern_status": before}, after_json={"pattern_status": pat.pattern_status},
        reason=req.reason, actor_id=req.reviewer_id,
    ))
    await session.commit()
    return PatternReviewResponse(pattern_id=str(pat.id), pattern_status=pat.pattern_status)


@router.post("/web-patterns/{pattern_id}/block", response_model=PatternReviewResponse)
async def block_pattern(
    pattern_id: str,
    req: PatternReviewRequest,
    session: AsyncSession = Depends(get_session),
) -> PatternReviewResponse:
    """§6.4 패턴 차단 → pattern_status=blocked + 감사."""
    pid = _as_uuid(pattern_id)
    pat = (await session.execute(select(WebPattern).where(WebPattern.id == pid))).scalar_one_or_none()
    if pat is None:
        raise HTTPException(status_code=404, detail="pattern not found")
    before = pat.pattern_status
    pat.pattern_status = PatternStatus.blocked.value
    session.add(WebAuditLog(
        action="pattern.block", pattern_id=pat.id, source_id=pat.source_id,
        before_json={"pattern_status": before}, after_json={"pattern_status": pat.pattern_status},
        reason=req.reason, actor_id=req.reviewer_id,
    ))
    await session.commit()
    return PatternReviewResponse(pattern_id=str(pat.id), pattern_status=pat.pattern_status)


@router.post("/web-sources/{source_id}/apply-delete", response_model=ApplyDeleteResponse)
async def apply_delete_route(
    source_id: str,
    req: ApplyDeleteRequest,
    session: AsyncSession = Depends(get_session),
) -> ApplyDeleteResponse:
    """삭제 전파(§6.5): 패턴 blocked·embedding 무효화·스냅샷 차단·삭제요청 resolved."""
    sid = _as_uuid(source_id)
    source = (await session.execute(select(WebSource).where(WebSource.id == sid))).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    rid = _as_uuid(req.request_id) if req.request_id else None
    out = await apply_delete(session, source_id=sid, request_id=rid, actor_id=req.actor_id)
    await session.commit()
    return ApplyDeleteResponse(
        source_id=out["source_id"],
        patterns_blocked=out["patterns_blocked"],
        snapshots_blocked=out["snapshots_blocked"],
        status=out["status"],
    )


@router.post("/web-sources/{source_id}/recheck", response_model=RecheckResponse)
async def recheck_route(
    source_id: str,
    req: RecheckRequest,
    session: AsyncSession = Depends(get_session),
) -> RecheckResponse:
    """재검증(§11): 결과별 처리(unchanged/hold/removed). 알 수 없는 result 는 400."""
    sid = _as_uuid(source_id)
    source = (await session.execute(select(WebSource).where(WebSource.id == sid))).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    try:
        out = await apply_recheck_result(session, source_id=sid, result=req.result, actor_id=req.actor_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await session.commit()
    return RecheckResponse(source_id=str(sid), action=out["action"], patterns_blocked=out["patterns_blocked"])