"""Pattern Build 영속화 서비스 (§5 / §4.3).

GovernanceDecision(app/pipeline.py) → web_patterns 행으로 저장. feature_json 에는
**추상화본**(원본 표현 제거)을 저장하고, reuse 점수·하드룰·recon 결과·pattern_status 를
계약 컬럼에 매핑한다. brand_risk_lookup 은 호출부가 프리페치해 주입.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import WebPattern
from app.pipeline import run_pattern_governance, GovernanceDecision
from app.pattern.reuse_risk import BrandRiskLookup


def _as_uuid(value) -> uuid.UUID:
    """입력값을 UUID 로 정규화. UUID 면 그대로, str 이면 uuid.UUID(value)."""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(value)


async def persist_pattern(
    session: AsyncSession,
    *,
    source_id,
    decision: GovernanceDecision,
    pattern_type: str,
    license_status: str,
    abstraction_level: str = "structural",
    org_id=None,
    quality_score: float | None = None,
    version: str = "1.0.0",
) -> WebPattern:
    """GovernanceDecision 을 WebPattern 행으로 영속화·flush·반환.

    feature_json 에는 원본이 아니라 decision.abstracted_feature(추상화본)를 저장하되,
    `_` 접두 내부키(예: _leak_probe — 누출검사용 원문 비교 컨텍스트)는 영속화에서 제외해
    원문이 DB 에 남지 않도록 한다.
    """
    feature_json = {
        k: v for k, v in decision.abstracted_feature.items() if not k.startswith("_")
    }
    pattern = WebPattern(
        source_id=_as_uuid(source_id),
        org_id=_as_uuid(org_id) if org_id else None,
        pattern_type=pattern_type,
        abstraction_level=abstraction_level,
        original_reuse_risk=decision.original_reuse_risk,
        reuse_subscores=decision.reuse_subscores,
        reuse_score=decision.reuse_score,
        reuse_hardrule=decision.reuse_hardrule,
        recon_test_passed=decision.recon_test_passed,
        feature_json=feature_json,
        license_status=license_status,
        pii_status=decision.pii_status,
        quality_score=quality_score,
        pattern_status=decision.pattern_status,
        version=version,
    )
    session.add(pattern)
    await session.flush()
    return pattern


async def build_and_persist_pattern(
    session: AsyncSession,
    *,
    source_id,
    raw_feature: dict,
    pattern_type: str,
    license_status: str,
    brand_risk_lookup: BrandRiskLookup | None = None,
    pii_text: str | None = None,
    abstraction_level: str = "structural",
    org_id=None,
    quality_score: float | None = None,
    version: str = "1.0.0",
) -> tuple[WebPattern, GovernanceDecision]:
    """원시 feature → 거버넌스 결정 → 영속화. (pattern, decision) 반환."""
    decision = run_pattern_governance(
        raw_feature,
        brand_risk_lookup=brand_risk_lookup,
        pii_text=pii_text,
    )
    pattern = await persist_pattern(
        session,
        source_id=source_id,
        decision=decision,
        pattern_type=pattern_type,
        license_status=license_status,
        abstraction_level=abstraction_level,
        org_id=org_id,
        quality_score=quality_score,
        version=version,
    )
    return pattern, decision


async def get_pattern(session: AsyncSession, pattern_id) -> WebPattern | None:
    """pattern_id 로 WebPattern 조회. 없으면 None."""
    stmt = select(WebPattern).where(WebPattern.id == _as_uuid(pattern_id))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()