"""워커 runner — crawl_job 완전 자동 연결.

crawl_job 하나를 받아 fetch(worker) → parse → 거버넌스 → web_patterns 영속화까지
한 번에 묶는다(§8 worker 생명주기 + §9 거버넌스 + §5 영속화). 워커는 주입형(덕타이핑).

선택적 crawl_job(ORM 행) 주입 시 §4.2 상태 전이 영속화:
running → succeeded / failed_retryable(일시 오류) / failed_terminal(영구 오류).
선택적 publisher 주입 시 §7 이벤트 발행: crawl.started/completed/failed, pattern.built/approved/blocked.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.workers.html_parser import parse_html
from app.pattern_build import build_and_persist_pattern
from app.pattern.reuse_risk import BrandRiskLookup
from app.models.enums import CrawlJobStatus
from app.job_state import (
    mark_running,
    mark_succeeded,
    mark_failed_retryable,
    mark_failed_terminal,
)
from app.events import (
    make_event,
    TOPIC_CRAWL_STARTED,
    TOPIC_CRAWL_COMPLETED,
    TOPIC_CRAWL_FAILED,
    TOPIC_PATTERN_BUILT,
    TOPIC_PATTERN_APPROVED,
    TOPIC_PATTERN_BLOCKED,
)

# postcheck 실패 사유 중 일시 오류(재시도 가능). 그 외는 영구 오류(terminal).
_RETRYABLE_REASONS = frozenset({"fetch_failed", "render_failed"})


async def process_crawl_job(
    session: AsyncSession,
    *,
    job: dict[str, Any],
    worker,
    source_id,
    license_status: str,
    parser=parse_html,
    pattern_type: str = "html_layout",
    brand_risk_lookup: BrandRiskLookup | None = None,
    crawl_job=None,
    publisher=None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """crawl_job → fetch → parse → 거버넌스 → web_patterns 영속화.

    실패 단계는 stage(precheck/postcheck)로 조기 반환. crawl_job 주입 시 상태 전이,
    publisher 주입 시 §7 이벤트도 발행.
    """
    job_id = str(crawl_job.id) if crawl_job is not None else None

    async def _emit(topic, *, status=None, pattern_id=None, payload=None):
        if publisher is not None:
            await publisher.publish(topic, make_event(
                topic, source_id=source_id, job_id=job_id, pattern_id=pattern_id,
                trace_id=trace_id, status=status, payload=payload,
            ))

    # crawl_job 주입 시: 처리 시작 → running(이미 running이면 생략).
    if crawl_job is not None and crawl_job.status != CrawlJobStatus.running.value:
        await mark_running(session, crawl_job)
    await _emit(TOPIC_CRAWL_STARTED, status="running")

    # 1) precheck
    pc = worker.precheck(job)
    if not pc.get("ok"):
        if crawl_job is not None:
            await mark_failed_terminal(session, crawl_job, error_code="precheck", error_message=pc.get("reason"))
        await _emit(TOPIC_CRAWL_FAILED, status="failed", payload={"stage": "precheck", "reason": pc.get("reason")})
        return {"ok": False, "stage": "precheck", "reason": pc.get("reason"), "pattern_id": None}

    # 2) execute(fetch)
    res = worker.execute(job)

    # 3) postcheck
    poc = worker.postcheck(res)
    if not poc.get("ok"):
        reason = poc.get("reason")
        if crawl_job is not None:
            if reason in _RETRYABLE_REASONS:
                await mark_failed_retryable(session, crawl_job, error_code="postcheck", error_message=reason)
            else:
                await mark_failed_terminal(session, crawl_job, error_code="postcheck", error_message=reason)
        await _emit(TOPIC_CRAWL_FAILED, status="failed", payload={"stage": "postcheck", "reason": reason})
        return {"ok": False, "stage": "postcheck", "reason": reason, "pattern_id": None}

    # 4) parse → raw_feature.
    #    워커가 feature 를 이미 산출(PDFParseWorker)했으면 그대로, 아니면 parser 로 text → feature.
    feature = res.get("feature")
    if feature is not None:
        raw_feature = feature
    else:
        raw_feature = parser(res.get("text") or "", url=res.get("url"))

    # 5) 거버넌스 + 영속화
    pattern, decision = await build_and_persist_pattern(
        session,
        source_id=source_id,
        raw_feature=raw_feature,
        pattern_type=pattern_type,
        license_status=license_status,
        brand_risk_lookup=brand_risk_lookup,
    )
    pattern_id = str(pattern.id)

    # 6) 이벤트(pattern.*) + crawl_job 성공 전이 + crawl.completed
    await _emit(TOPIC_PATTERN_BUILT, status=decision.pattern_status, pattern_id=pattern_id)
    if decision.operational:
        await _emit(TOPIC_PATTERN_APPROVED, status="approved", pattern_id=pattern_id)
    else:
        await _emit(TOPIC_PATTERN_BLOCKED, status="blocked", pattern_id=pattern_id,
                    payload={"blocked_reason": decision.blocked_reason})

    if crawl_job is not None:
        await mark_succeeded(session, crawl_job)
    await _emit(TOPIC_CRAWL_COMPLETED, status="succeeded", pattern_id=pattern_id)

    return {
        "ok": True,
        "stage": "persisted",
        "reason": None,
        "pattern_id": pattern_id,
        "operational": decision.operational,
        "pattern_status": decision.pattern_status,
        "blocked_reason": decision.blocked_reason,
    }