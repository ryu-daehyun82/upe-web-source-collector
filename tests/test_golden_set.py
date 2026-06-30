"""Golden Set 릴리즈 게이트(§16 G4). critical 라벨 recall=100% 강제.

가중치/임계(reuse_risk.py)나 G4(reconstruction)를 바꿔 critical 샘플을 놓치면
이 테스트가 실패한다 — 법적 방어선 회귀 방지.
"""
from app.golden_set import (
    evaluate_sample, evaluate_golden_set,
    GoldenSample, GOLDEN_SAMPLES, EXPECTED_GRADES, CRITICAL_LABELS,
)


def test_golden_set_passes():
    report = evaluate_golden_set()
    assert report.passed is True
    assert report.critical_recall_ok is True


def test_critical_labels_recall_100():
    report = evaluate_golden_set()
    for label in CRITICAL_LABELS:
        assert report.recall_by_label.get(label) == 1.0, f"{label} recall < 1.0"


def test_all_samples_pass_expected():
    # 정본 골든셋의 모든 샘플이 기대 등급 안에 들어야 한다(보정 완료 상태)
    report = evaluate_golden_set()
    failed = [(r.label, r.actual, r.expected) for r in report.results if not r.passed]
    assert failed == [], f"golden samples off-grade: {failed}"


def test_safe_structural_low():
    for s in GOLDEN_SAMPLES:
        if s.label == "safe-structural":
            assert evaluate_sample(s).actual == "low"


def test_text_leak_blocked_hardrule():
    for s in GOLDEN_SAMPLES:
        if s.label == "text-leak":
            assert evaluate_sample(s).actual == "blocked"


def test_logo_face_at_least_high():
    for s in GOLDEN_SAMPLES:
        if s.label == "logo-face":
            assert evaluate_sample(s).actual in {"high", "blocked"}


def test_brand_clone_high_or_blocked():
    for s in GOLDEN_SAMPLES:
        if s.label == "brand-clone":
            assert evaluate_sample(s).actual in {"high", "blocked"}


def test_expected_grades_cover_all_labels():
    labels = {s.label for s in GOLDEN_SAMPLES}
    assert labels <= set(EXPECTED_GRADES)


def test_recall_drops_when_sample_broken():
    # 의도적으로 약한 brand-clone(시각신호 0) → low → passed False → recall<1.0
    broken = [GoldenSample("brand-clone", {"layout_similarity": 0.0, "color_signature": 0.0,
                                           "structure_fingerprint": 0.0, "brand_risk": 0.0})]
    report = evaluate_golden_set(broken)
    assert report.recall_by_label["brand-clone"] == 0.0
    assert report.passed is False


def test_evaluate_sample_unknown_label_fails():
    # EXPECTED_GRADES에 없는 라벨 → expected 비어 passed False
    r = evaluate_sample(GoldenSample("nonexistent", {"layout_similarity": 0.1}))
    assert r.passed is False
    assert r.expected == []