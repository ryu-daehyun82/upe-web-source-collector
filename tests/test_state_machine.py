"""상태기계 계약 테스트 (Sprint 0). 로직 본체 미구현이라 계약/전이표만 검증."""
from app.models.enums import (
    LicenseStatus,
    OPERATIONAL_LICENSE_STATES,
    OPERATIONAL_PII_STATES,
    OPERATIONAL_REUSE_STATES,
    OPERATIONAL_SOURCE_STATES,
    PatternStatus,
    ReuseRisk,
    SourceStatus,
)
from app.policy.license_state import ALLOWED_TRANSITIONS, can_transition


def test_license_blocked_is_terminal():
    assert ALLOWED_TRANSITIONS[LicenseStatus.blocked] == set()
    assert not can_transition(LicenseStatus.blocked, LicenseStatus.allowed)


def test_license_unknown_can_branch():
    assert can_transition(LicenseStatus.unknown, LicenseStatus.allowed)
    assert can_transition(LicenseStatus.unknown, LicenseStatus.conditional)
    assert can_transition(LicenseStatus.unknown, LicenseStatus.blocked)


def test_conditional_to_approved():
    assert can_transition(LicenseStatus.conditional, LicenseStatus.conditional_approved)


def test_operational_gate_sets():
    # 설계서 §4.3 운영 사용 가능 조건이 enum 으로 일관되게 정의됐는지
    assert SourceStatus.parsed in OPERATIONAL_SOURCE_STATES
    assert LicenseStatus.allowed in OPERATIONAL_LICENSE_STATES
    assert ReuseRisk.high not in OPERATIONAL_REUSE_STATES
    assert ReuseRisk.low in OPERATIONAL_REUSE_STATES
    assert len(OPERATIONAL_PII_STATES) == 2


def test_pattern_status_has_risk_scored():
    assert PatternStatus.reuse_risk_scored.value == "reuse_risk_scored"
