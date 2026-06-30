"""통합 운영게이트 (설계 §4.3 + v2.1 P-9 G4).

패턴이 생성엔진에 전달 가능한지(operational)를 6개 게이트로 종합 판정:
source.crawl_status · license_status · pii_status · original_reuse_risk · pattern_status(approved)
+ recon_test_passed(True). 미충족 게이트 이름을 reasons 로 수집.

pipeline.GovernanceDecision 은 "패턴 내재 안전성"(pii·reuse·recon)만 보지만, 본 게이트는
license·source.status·pattern_status 까지 합쳐 §4.3 전체 조건을 한 곳에서 강제한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import (
    PatternStatus,
    OPERATIONAL_SOURCE_STATES,
    OPERATIONAL_LICENSE_STATES,
    OPERATIONAL_PII_STATES,
    OPERATIONAL_REUSE_STATES,
)


def _val(x):
    """값 정규화: enum 이면 .value, 그 외(str/None)는 그대로."""
    return x.value if hasattr(x, "value") else x


_SOURCE_OK = frozenset(s.value for s in OPERATIONAL_SOURCE_STATES)
_LICENSE_OK = frozenset(s.value for s in OPERATIONAL_LICENSE_STATES)
_PII_OK = frozenset(s.value for s in OPERATIONAL_PII_STATES)
_REUSE_OK = frozenset(s.value for s in OPERATIONAL_REUSE_STATES)
_PATTERN_OK = PatternStatus.approved.value


@dataclass
class OperationalGateResult:
    """운영게이트 평가 결과. reasons = 미충족 게이트 이름 목록."""
    operational: bool
    reasons: list[str] = field(default_factory=list)


def evaluate_operational(
    *,
    source_status,
    license_status,
    pii_status,
    original_reuse_risk,
    pattern_status,
    recon_test_passed=None,
) -> OperationalGateResult:
    """6개 게이트 종합. 전부 통과면 operational=True, 아니면 미충족 이름을 reasons 에 수집.

    게이트 이름: source_status / license_status / pii_status / reuse_risk / pattern_status / recon_test.
    각 입력은 str 또는 enum 허용(_val 정규화). recon_test_passed 는 True 여야 통과(None/False 실패).
    """
    reasons: list[str] = []

    if _val(source_status) not in _SOURCE_OK:
        reasons.append("source_status")
    if _val(license_status) not in _LICENSE_OK:
        reasons.append("license_status")
    if _val(pii_status) not in _PII_OK:
        reasons.append("pii_status")
    if _val(original_reuse_risk) not in _REUSE_OK:
        reasons.append("reuse_risk")
    if _val(pattern_status) != _PATTERN_OK:
        reasons.append("pattern_status")
    if recon_test_passed is not True:
        reasons.append("recon_test")

    return OperationalGateResult(operational=not reasons, reasons=reasons)


def evaluate_pattern(pattern, source) -> OperationalGateResult:
    """WebPattern + WebSource ORM 행에서 §4.3 필드를 뽑아 evaluate_operational 호출(덕타이핑)."""
    return evaluate_operational(
        source_status=source.crawl_status,
        license_status=pattern.license_status,
        pii_status=pattern.pii_status,
        original_reuse_risk=pattern.original_reuse_risk,
        pattern_status=pattern.pattern_status,
        recon_test_passed=pattern.recon_test_passed,
    )
