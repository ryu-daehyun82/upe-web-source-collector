"""Pattern 상태기계 테스트 (설계서 §4 / §9).

전이표 계약 + Risk Score → pattern_status 자동결정(high/blocked 면 approved 불가).
"""
import pytest

from app.models.enums import PatternStatus, ReuseRisk
from app.pattern.pattern_state import (
    PATTERN_TRANSITIONS,
    can_approve,
    can_transition,
    decide_pattern_status,
    transition,
)


# ----------------------------------------------------------------------------
# 전이표 (계약)
# ----------------------------------------------------------------------------
def test_happy_path_transitions():
    assert can_transition(PatternStatus.built, PatternStatus.abstraction_checked)
    assert can_transition(PatternStatus.abstraction_checked, PatternStatus.reuse_risk_scored)
    assert can_transition(PatternStatus.reuse_risk_scored, PatternStatus.approved)


def test_cannot_skip_stages():
    assert not can_transition(PatternStatus.built, PatternStatus.approved)
    assert not can_transition(PatternStatus.built, PatternStatus.reuse_risk_scored)


def test_blocked_and_deprecated_terminal():
    assert PATTERN_TRANSITIONS[PatternStatus.blocked] == set()
    assert PATTERN_TRANSITIONS[PatternStatus.deprecated] == set()
    assert not can_transition(PatternStatus.blocked, PatternStatus.approved)


def test_can_block_from_any_active_stage():
    for src in (PatternStatus.built, PatternStatus.abstraction_checked,
                PatternStatus.reuse_risk_scored, PatternStatus.approved):
        assert can_transition(src, PatternStatus.blocked)


def test_transition_raises_on_illegal():
    with pytest.raises(ValueError):
        transition(PatternStatus.built, PatternStatus.approved)
    assert transition(PatternStatus.built, PatternStatus.abstraction_checked) == (
        PatternStatus.abstraction_checked
    )


# ----------------------------------------------------------------------------
# Risk Score → pattern_status 자동결정
# ----------------------------------------------------------------------------
def test_low_medium_approved():
    assert decide_pattern_status(ReuseRisk.low) == PatternStatus.approved
    assert decide_pattern_status(ReuseRisk.medium) == PatternStatus.approved


def test_high_blocked_cannot_approve():
    assert decide_pattern_status(ReuseRisk.high) == PatternStatus.blocked
    assert decide_pattern_status(ReuseRisk.blocked) == PatternStatus.blocked


def test_recon_fail_forces_blocked_even_if_low():
    # 점수 낮아도 역복원 가능(G4 실패) → blocked.
    assert decide_pattern_status(ReuseRisk.low, recon_test_passed=False) == PatternStatus.blocked


def test_recon_pass_or_none_allows_approve():
    assert decide_pattern_status(ReuseRisk.low, recon_test_passed=True) == PatternStatus.approved
    assert decide_pattern_status(ReuseRisk.medium, recon_test_passed=None) == PatternStatus.approved


def test_can_approve_helper():
    assert can_approve(ReuseRisk.low) is True
    assert can_approve(ReuseRisk.medium) is True
    assert can_approve(ReuseRisk.high) is False
    assert can_approve(ReuseRisk.blocked) is False
    assert can_approve(ReuseRisk.low, recon_test_passed=False) is False


def test_terminal_state_idempotent():
    assert decide_pattern_status(
        ReuseRisk.low, current=PatternStatus.blocked
    ) == PatternStatus.blocked
    assert decide_pattern_status(
        ReuseRisk.low, current=PatternStatus.deprecated
    ) == PatternStatus.deprecated
