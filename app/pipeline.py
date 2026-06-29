"""Pattern Governance 파이프라인 (§9 / §13 통합).

지금까지의 모듈을 end-to-end 로 연결하는 핵심 IP 경로:
  abstraction_guard(원본 제거) → PII 스캔 → visual flags(logo/artwork)
  → Reuse Risk Score → G4 Reconstruction Test → 최종 게이트(생성엔진 전달 가부).

순수(네트워크/DB 없음). brand_risk 는 lookup 콜러블 주입.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import (
    PatternStatus,
    ReuseRisk,
    OPERATIONAL_PII_STATES,
    OPERATIONAL_REUSE_STATES,
)
from app.pattern.abstraction_guard import guard
from app.pattern.reuse_risk import compute_reuse_risk, BrandRiskLookup
from app.pattern.reconstruction_test import run_reconstruction_test
from app.adapters.pii import get_pii_scanner
from app.adapters.vision import detect_visual_flags


@dataclass
class GovernanceDecision:
    """거버넌스 파이프라인 결과(생성엔진 전달 가부 + 단계별 산출)."""
    operational: bool
    pattern_status: str
    blocked_reason: str | None
    original_reuse_risk: str
    reuse_score: float
    reuse_hardrule: str | None
    reuse_subscores: dict
    recon_test_passed: bool
    recon_fail_reason: str | None
    pii_status: str
    pii_types: list[str]
    abstracted_feature: dict
    removed_items: list[str]


def run_pattern_governance(
    raw_feature: dict,
    *,
    brand_risk_lookup: BrandRiskLookup | None = None,
    pii_text: str | None = None,
) -> GovernanceDecision:
    """원시 feature → 거버넌스 결정.

    raw_feature 엔 점수 입력(layout/color/structure/original_text/pattern_text/
    domain/brand_risk/검출 메타)이 들어온다.

    단계: 1) abstraction guard 2) PII 3) visual flags 4) reuse risk 5) G4 recon
          6) 최종 게이트(pii∈{clean,redacted} & reuse∈{low,medium} & recon_passed).
    blocked_reason 우선순위: pii_sensitive > reuse_blocked > reuse_high > recon_failed.
    """
    # 1) Abstraction Guard — 저장 대상은 abstracted(원본 표현 제거).
    abstracted, removed = guard(raw_feature)

    # 2) PII 스캔(원문 대상). pii_text 우선, 없으면 original_text.
    pii_text_target = pii_text if pii_text is not None else raw_feature.get("original_text")
    pii_result = get_pii_scanner().scan(pii_text_target)
    pii_status = pii_result.status
    pii_types = sorted(pii_result.types())

    # 3) Visual flags(logo/artwork) — raw 메타 기반.
    visual_flags = detect_visual_flags(raw_feature)

    # 4~5) 점수/recon 은 원문 비교가 필요 → stripped 아닌 raw 기반 scoring 사용.
    scoring_feature = dict(raw_feature)
    scoring_feature.update(visual_flags)

    # 4) Reuse Risk Score.
    risk = compute_reuse_risk(scoring_feature, brand_risk_lookup=brand_risk_lookup)
    reuse_risk = risk["reuse_risk"]

    # 5) G4 Reconstruction Test.
    recon = run_reconstruction_test(scoring_feature)
    recon_test_passed = recon["recon_test_passed"]

    # 6) 최종 게이트.
    pii_ok = pii_status in OPERATIONAL_PII_STATES
    reuse_ok = reuse_risk in OPERATIONAL_REUSE_STATES
    recon_ok = recon_test_passed

    if pii_ok and reuse_ok and recon_ok:
        operational = True
        pattern_status = PatternStatus.approved.value
        blocked_reason = None
    else:
        operational = False
        pattern_status = PatternStatus.blocked.value
        if not pii_ok:
            blocked_reason = "pii_sensitive"
        elif reuse_risk == ReuseRisk.blocked:
            blocked_reason = "reuse_blocked"
        elif not reuse_ok:
            blocked_reason = "reuse_high"
        elif not recon_ok:
            blocked_reason = "recon_failed"
        else:
            blocked_reason = "unknown"

    return GovernanceDecision(
        operational=operational,
        pattern_status=pattern_status,
        blocked_reason=blocked_reason,
        original_reuse_risk=reuse_risk.value,
        reuse_score=risk["reuse_score"],
        reuse_hardrule=risk["reuse_hardrule"],
        reuse_subscores=risk["subscores"],
        recon_test_passed=recon_test_passed,
        recon_fail_reason=recon["fail_reason"],
        pii_status=pii_status.value,
        pii_types=pii_types,
        abstracted_feature=abstracted,
        removed_items=removed,
    )


def run_batch(
    raw_features: list[dict],
    *,
    brand_risk_lookup: BrandRiskLookup | None = None,
) -> list[GovernanceDecision]:
    """배치 처리. 각 feature 를 run_pattern_governance(pii_text 기본=original_text)로."""
    return [
        run_pattern_governance(feature, brand_risk_lookup=brand_risk_lookup)
        for feature in raw_features
    ]