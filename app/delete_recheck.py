"""삭제/재검증 워크플로우 (§11 / §6.5).

삭제 요청 전파(source→delete_requested, 패턴 blocked + embedding 무효화, 스냅샷 접근차단,
삭제요청 resolved, 감사로그, 이벤트) + 라이선스 재검증(주기 판정 + 결과별 처리:
unchanged/hold/removed). 모든 부수효과는 감사로그(WebAuditLog)로 남긴다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import WebSource, WebPattern, WebDeleteRequest, WebAuditLog, CrawlSnapshot
from app.models.enums import SourceStatus, PatternStatus
from app.events import make_event, TOPIC_DELETE_REQUESTED, TOPIC_DELETE_COMPLETED

# §11.1 재검증 주기(일)
RECHECK_INTERVAL_DAYS: dict[str, int] = {
    "partner": 90,     # 협약/자사
    "public": 180,     # 공공기관
    "general": 30,     # 일반 웹
    "high_risk": 7,    # 고위험
}

# §11.2 재검증 결과 → 처리 분류
RECHECK_HOLD = frozenset({"license_changed", "robots_changed", "content_changed", "manual_review_required"})
RECHECK_OK = frozenset({"unchanged"})
RECHECK_REMOVED = frozenset({"content_removed"})


async def _write_audit(
    session: AsyncSession,
    *,
    action: str,
    source_id=None,
    pattern_id=None,
    reason: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    actor_id: str | None = None,
    trace_id: str | None = None,
) -> WebAuditLog:
    """WebAuditLog 행 생성·add·flush·반환."""
    audit_log = WebAuditLog(
        actor_id=actor_id,
        action=action,
        source_id=source_id,
        pattern_id=pattern_id,
        before_json=before,
        after_json=after,
        reason=reason,
        trace_id=trace_id,
    )
    session.add(audit_log)
    await session.flush()
    return audit_log


async def _block_patterns(session: AsyncSession, source_id) -> int:
    """source 의 모든 WebPattern → pattern_status=blocked, embedding=None. 변경 개수 반환."""
    result = await session.execute(select(WebPattern).where(WebPattern.source_id == source_id))
    patterns = result.scalars().all()
    count = 0
    for pattern in patterns:
        if pattern.pattern_status != PatternStatus.blocked.value or pattern.embedding is not None:
            pattern.pattern_status = PatternStatus.blocked.value
            pattern.embedding = None
            count += 1
    if count:
        await session.flush()
    return count


async def _restrict_snapshots(session: AsyncSession, source_id) -> int:
    """source 의 CrawlSnapshot.access_level="blocked". 변경 개수 반환."""
    result = await session.execute(select(CrawlSnapshot).where(CrawlSnapshot.source_id == source_id))
    snapshots = result.scalars().all()
    count = 0
    for snap in snapshots:
        if snap.access_level != "blocked":
            snap.access_level = "blocked"
            count += 1
    if count:
        await session.flush()
    return count


async def request_delete(
    session: AsyncSession,
    *,
    source_id,
    request_type: str,
    requester: str | None = None,
    requester_contact: str | None = None,
    reason: str | None = None,
) -> WebDeleteRequest:
    """삭제 요청 접수(상태 received). WebDeleteRequest 생성·flush·반환."""
    delete_request = WebDeleteRequest(
        source_id=source_id,
        requester=requester,
        requester_contact=requester_contact,
        request_type=request_type,
        reason=reason,
        status="received",
    )
    session.add(delete_request)
    await session.flush()
    return delete_request


async def apply_delete(
    session: AsyncSession,
    *,
    source_id,
    request_id=None,
    actor_id: str | None = None,
    trace_id: str | None = None,
    publisher=None,
    now: datetime | None = None,
) -> dict:
    """삭제 전파(§6.5). source→delete_requested, 패턴 blocked+embedding None, 스냅샷 차단,
    삭제요청 resolved, 감사로그, 이벤트(delete.requested/completed) 발행."""
    if now is None:
        now = datetime.now(timezone.utc)

    source = await session.get(WebSource, source_id)
    if source is not None:
        source.crawl_status = SourceStatus.delete_requested.value

    patterns_blocked = await _block_patterns(session, source_id)
    snapshots_blocked = await _restrict_snapshots(session, source_id)

    if request_id is not None:
        dr = (await session.execute(
            select(WebDeleteRequest).where(WebDeleteRequest.id == request_id)
        )).scalar_one_or_none()
        if dr is not None:
            dr.status = "resolved"
            dr.resolved_at = now
            dr.resolution_note = "propagated"

    await _write_audit(
        session,
        action="delete_propagated",
        source_id=source_id,
        reason=f"patterns_blocked={patterns_blocked}, snapshots_blocked={snapshots_blocked}",
        actor_id=actor_id,
        trace_id=trace_id,
    )

    if publisher is not None:
        payload = {"patterns_blocked": patterns_blocked, "snapshots_blocked": snapshots_blocked}
        for topic, status in ((TOPIC_DELETE_REQUESTED, "delete_requested"), (TOPIC_DELETE_COMPLETED, "completed")):
            await publisher.publish(topic, make_event(
                topic, source_id=source_id, trace_id=trace_id, status=status, payload=payload,
            ))

    return {
        "source_id": str(source_id),
        "patterns_blocked": patterns_blocked,
        "snapshots_blocked": snapshots_blocked,
        "status": "delete_requested",
    }


def is_recheck_due(last_checked_at: datetime | None, category: str, *, now: datetime | None = None) -> bool:
    """재검증 도래 여부. last_checked_at None이면 True. 없는 category 는 'general'.

    now - last_checked_at >= RECHECK_INTERVAL_DAYS[category] 일이면 True.
    tz-naive last_checked_at 은 utc 로 가정 보정.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if last_checked_at is None:
        return True

    interval = RECHECK_INTERVAL_DAYS.get(category, RECHECK_INTERVAL_DAYS["general"])
    if last_checked_at.tzinfo is None:
        last_checked_at = last_checked_at.replace(tzinfo=timezone.utc)
    return now - last_checked_at >= timedelta(days=interval)


async def apply_recheck_result(
    session: AsyncSession,
    *,
    source_id,
    result: str,
    actor_id: str | None = None,
    trace_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    """재검증 결과 처리(§11.2). source.last_checked_at 갱신 + 결과별 처리.

    unchanged → none / HOLD(license·robots·content·manual) → 패턴 blocked(hold) /
    content_removed → source blocked + 패턴·스냅샷 차단(removed). 그 외 ValueError.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    source = await session.get(WebSource, source_id)
    if source is not None:
        source.last_checked_at = now

    if result in RECHECK_OK:
        await _write_audit(session, action="recheck_unchanged", source_id=source_id, reason=result,
                           actor_id=actor_id, trace_id=trace_id)
        return {"action": "none", "patterns_blocked": 0}

    if result in RECHECK_HOLD:
        patterns_blocked = await _block_patterns(session, source_id)
        await _write_audit(session, action="recheck_hold", source_id=source_id, reason=result,
                           actor_id=actor_id, trace_id=trace_id)
        return {"action": "hold", "patterns_blocked": patterns_blocked}

    if result in RECHECK_REMOVED:
        if source is not None:
            source.crawl_status = SourceStatus.blocked.value
        patterns_blocked = await _block_patterns(session, source_id)
        snapshots_blocked = await _restrict_snapshots(session, source_id)
        await _write_audit(session, action="recheck_removed", source_id=source_id, reason=result,
                           actor_id=actor_id, trace_id=trace_id)
        return {"action": "removed", "patterns_blocked": patterns_blocked, "snapshots_blocked": snapshots_blocked}

    raise ValueError(f"unknown recheck result: {result}")