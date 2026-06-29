"""워커 runner — crawl_job 완전 자동 연결.

crawl_job 하나를 받아 fetch(worker) → parse → 거버넌스 → web_patterns 영속화까지
한 번에 묶는다(§8 worker 생명주기 + §9 거버넌스 + §5 영속화). 워커는 주입형(덕타이핑).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.workers.html_parser import parse_html
from app.pattern_build import build_and_persist_pattern
from app.pattern.reuse_risk import BrandRiskLookup


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
) -> dict[str, Any]:
    """crawl_job → fetch → parse → 거버넌스 → web_patterns 영속화.

    실패 단계는 stage(precheck/postcheck)로 조기 반환. 성공 시 pattern_id 와
    거버넌스 결과(operational/pattern_status/blocked_reason) 반환.
    """
    # 1) precheck
    pc = worker.precheck(job)
    if not pc.get("ok"):
        return {"ok": False, "stage": "precheck", "reason": pc.get("reason"), "pattern_id": None}

    # 2) execute(fetch)
    res = worker.execute(job)

    # 3) postcheck
    poc = worker.postcheck(res)
    if not poc.get("ok"):
        return {"ok": False, "stage": "postcheck", "reason": poc.get("reason"), "pattern_id": None}

    # 4) parse → raw_feature
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

    # 6) 결과
    return {
        "ok": True,
        "stage": "persisted",
        "reason": None,
        "pattern_id": str(pattern.id),
        "operational": decision.operational,
        "pattern_status": decision.pattern_status,
        "blocked_reason": decision.blocked_reason,
    }