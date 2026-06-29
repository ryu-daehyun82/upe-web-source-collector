"""Pattern 상태기계 (설계서 §4 / §9).

전이표(계약): built → abstraction_checked → reuse_risk_scored → approved/blocked/deprecated.
+ Risk Score 결과로 pattern_status 자동결정(high/blocked risk 면 approved 불가, blocked).

순수 모듈(외부 의존 없음). 전이표는 license_state.ALLOWED_TRANSITIONS 패턴 미러.
"""
from __future__ import annotations

from app.models.enums import PatternStatus, ReuseRisk

# ----------------------------------------------------------------------------
# 허용 전이표 (계약)
# ----------------------------------------------------------------------------
PATTERN_TRANSITIONS: dict[PatternStatus, set[PatternStatus]] = {
    PatternStatus.built: {
        PatternStatus.abstraction_checked,
        PatternStatus.blocked,
        PatternStatus.deprecated,
    },
    PatternStatus.abstraction_checked: {
        PatternStatus.reuse_risk_scored,
        PatternStatus.blocked,
        PatternStatus.deprecated,
    },
    PatternStatus.reuse_risk_scored: {
        PatternStatus.approved,
        PatternStatus.blocked,
        PatternStatus.deprecated,
    },
    PatternStatus.approved: {
        PatternStatus.deprecated,
        PatternStatus.blocked,
    },
    PatternStatus.blocked: set(),       # 종단
    PatternStatus.deprecated: set(),    # 종단
}


def can_transition(src: PatternStatus, dst: PatternStatus) -> bool:
    """전이 허용 여부(계약). 위반 시 호출부에서 거부."""
    return dst in PATTERN_TRANSITIONS.get(src, set())


def transition(src: PatternStatus, dst: PatternStatus) -> PatternStatus:
    """전이 실행(검증 포함). 위반 시 ValueError."""
    if not can_transition(src, dst):
        raise ValueError(f"illegal pattern transition: {src.value} -> {dst.value}")
    return dst


# ----------------------------------------------------------------------------
# Risk Score → pattern_status 자동결정
# ----------------------------------------------------------------------------

#: approved 가능한 reuse 등급(스파이크 §3 / enums.OPERATIONAL_REUSE_STATES 정합).
#: high/blocked 는 approved 불가.
_APPROVABLE_REUSE = {ReuseRisk.low, ReuseRisk.medium}


def decide_pattern_status(
    reuse_risk: ReuseRisk,
    *,
    recon_test_passed: bool | None = None,
    current: PatternStatus = PatternStatus.reuse_risk_scored,
) -> PatternStatus:
    """Risk Score(+ recon test) 결과로 다음 pattern_status 자동결정.

    규칙(계약):
      - reuse_risk == blocked            → PatternStatus.blocked
      - reuse_risk in {high}             → blocked (approved 불가; 점수상 위험)
      - reuse_risk in {low, medium} 이고 recon_test_passed != False → approved
      - recon_test_passed == False       → blocked (역복원 가능 = 릴리즈 불가, G4)

    Args:
        reuse_risk: compute_reuse_risk 결과 등급.
        recon_test_passed: run_reconstruction_test 결과(None=미실행).
        current: 현재 상태(전이 가능성 검증용; 기본 reuse_risk_scored).

    Returns:
        결정된 PatternStatus (approved 또는 blocked).
    """
    # G4: 역복원 가능하면 등급과 무관하게 차단.
    if recon_test_passed is False:
        target = PatternStatus.blocked
    elif reuse_risk == ReuseRisk.blocked:
        target = PatternStatus.blocked
    elif reuse_risk not in _APPROVABLE_REUSE:  # high
        target = PatternStatus.blocked
    else:  # low / medium + recon ok(or 미실행)
        target = PatternStatus.approved

    # 전이 가능성 보장: 종단 상태에서 호출되면 그대로(멱등), 아니면 검증.
    if current in (PatternStatus.blocked, PatternStatus.deprecated):
        return current
    if target == current:
        return current
    if not can_transition(current, target):
        # 비정상 호출 — 보수적으로 blocked.
        return PatternStatus.blocked
    return target


def can_approve(reuse_risk: ReuseRisk, recon_test_passed: bool | None = None) -> bool:
    """approved 가능 여부 단축 판정(게이트용)."""
    if recon_test_passed is False:
        return False
    return reuse_risk in _APPROVABLE_REUSE
