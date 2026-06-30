"""Golden Set 보정·평가 하니스 (스파이크 §4 / §16 G4 골든테스트).

라벨된 고정 샘플(safe-structural/brand-clone/logo-face/text-leak/public-doc)을
reuse_risk 엔진에 통과시켜 라벨별 recall 을 계산하고, critical 라벨
(brand-clone/logo-face/text-leak) recall=100% 를 릴리즈 게이트로 강제한다.
가중치/임계 변경 시 이 하니스로 회귀 검증(놓치면 법적 사고).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import ReuseRisk
from app.pattern.reuse_risk import compute_reuse_risk, BrandRiskLookup

# 기대 등급(스파이크 §4)
EXPECTED_GRADES: dict[str, set[ReuseRisk]] = {
    "safe-structural": {ReuseRisk.low},
    "brand-clone": {ReuseRisk.high, ReuseRisk.blocked},
    "logo-face": {ReuseRisk.high, ReuseRisk.blocked},
    "text-leak": {ReuseRisk.blocked},
    "public-doc": {ReuseRisk.low, ReuseRisk.medium},
}

# 놓치면 법적 사고 → recall 100% 강제할 critical 라벨
CRITICAL_LABELS: frozenset[str] = frozenset({"brand-clone", "logo-face", "text-leak"})


@dataclass(frozen=True)
class GoldenSample:
    label: str
    feature: dict


GOLDEN_SAMPLES: list[GoldenSample] = [
    # safe-structural → low
    GoldenSample("safe-structural", {"layout_similarity": 0.2, "color_signature": 0.1, "structure_fingerprint": 0.2}),
    GoldenSample("safe-structural", {"layout_similarity": 0.15, "color_signature": 0.2, "structure_fingerprint": 0.1}),
    # brand-clone → high/blocked (강브랜드 layout+color 근접)
    GoldenSample("brand-clone", {"layout_similarity": 0.95, "color_signature": 0.95, "structure_fingerprint": 0.9, "brand_risk": 0.9}),
    GoldenSample("brand-clone", {"layout_similarity": 0.9, "color_signature": 0.92, "structure_fingerprint": 0.88, "brand_risk": 0.8}),
    # logo-face → ≥high (하드룰)
    GoldenSample("logo-face", {"logo_detected": True, "layout_similarity": 0.2, "color_signature": 0.2, "structure_fingerprint": 0.2}),
    GoldenSample("logo-face", {"face_detected": True, "layout_similarity": 0.1, "color_signature": 0.1, "structure_fingerprint": 0.1}),
    # text-leak → blocked (하드룰; 원문 잔존)
    GoldenSample("text-leak", {"original_text": "this exact original sentence leaks into the pattern verbatim now", "pattern_text": "this exact original sentence leaks into the pattern verbatim now"}),
    # public-doc → low/medium
    GoldenSample("public-doc", {"layout_similarity": 0.3, "color_signature": 0.2, "structure_fingerprint": 0.3, "brand_risk": 0.2}),
]


@dataclass
class SampleResult:
    label: str
    expected: list[str]   # 기대 등급 value 리스트
    actual: str           # 실제 등급 value
    passed: bool


@dataclass
class GoldenReport:
    results: list[SampleResult] = field(default_factory=list)
    recall_by_label: dict[str, float] = field(default_factory=dict)
    critical_recall_ok: bool = False
    passed: bool = False


def evaluate_sample(sample: GoldenSample, *, brand_risk_lookup: BrandRiskLookup | None = None) -> SampleResult:
    """compute_reuse_risk(sample.feature)["reuse_risk"] 와 기대 등급 비교. actual∈expected → passed."""
    expected_set = EXPECTED_GRADES.get(sample.label, set())
    expected = [e.value for e in expected_set]

    risk = compute_reuse_risk(sample.feature, brand_risk_lookup=brand_risk_lookup)["reuse_risk"]
    return SampleResult(
        label=sample.label,
        expected=expected,
        actual=risk.value,
        passed=risk in expected_set,
    )


def evaluate_golden_set(
    samples: list[GoldenSample] | None = None,
    *,
    brand_risk_lookup: BrandRiskLookup | None = None,
) -> GoldenReport:
    """골든셋 평가. 라벨별 recall + critical 라벨 recall=100% 게이트(passed)."""
    if samples is None:
        samples = GOLDEN_SAMPLES

    results = [evaluate_sample(s, brand_risk_lookup=brand_risk_lookup) for s in samples]

    counts: dict[str, int] = {}
    passed_counts: dict[str, int] = {}
    for r in results:
        counts[r.label] = counts.get(r.label, 0) + 1
        if r.passed:
            passed_counts[r.label] = passed_counts.get(r.label, 0) + 1

    recall_by_label = {label: passed_counts.get(label, 0) / n for label, n in counts.items()}

    critical_recall_ok = all(
        recall_by_label[label] == 1.0
        for label in CRITICAL_LABELS
        if label in recall_by_label
    )

    return GoldenReport(
        results=results,
        recall_by_label=recall_by_label,
        critical_recall_ok=critical_recall_ok,
        passed=critical_recall_ok,
    )