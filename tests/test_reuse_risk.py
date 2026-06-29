"""Reuse Risk Score 엔진 테스트 (스파이크 §1~§4).

순수 테스트(외부 의존 없음). golden set / 결합식 정확성 / 등급경계 / 하드룰.
핵심: brand-clone · text-leak recall = 100% (놓치면 법적 사고).
"""
import pytest

from app.models.enums import ReuseRisk
from app.pattern.reuse_risk import (
    GRADE_THRESHOLDS,
    HARDRULE_ARTWORK_FLOOR,
    HARDRULE_LOGO_FACE_FLOOR,
    HARDRULE_TEXT_OVERLAP,
    WEIGHTS,
    color_signature,
    compute_reuse_risk,
    layout_similarity,
    score_to_risk,
    structure_fingerprint,
    text_overlap,
)


# ----------------------------------------------------------------------------
# sub-score 단위
# ----------------------------------------------------------------------------
def test_text_overlap_identical_is_one():
    t = "the quick brown fox jumps over the lazy dog repeatedly every day"
    assert text_overlap(t, t) == pytest.approx(1.0)


def test_text_overlap_disjoint_is_zero():
    a = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
    b = "one two three four five six seven eight nine ten eleven"
    assert text_overlap(a, b) == 0.0


def test_text_overlap_none_is_zero():
    assert text_overlap(None, "anything here") == 0.0
    assert text_overlap("anything here", None) == 0.0


def test_text_overlap_partial_leak_detected():
    # 패턴에 원문 한 구절이 잔존 → > 0 누출.
    orig = "our exclusive premium membership unlocks special benefits for loyal customers today"
    pattern_leak = "our exclusive premium membership unlocks special benefits"
    assert text_overlap(orig, pattern_leak) > 0.0


def test_layout_similarity_precomputed():
    assert layout_similarity({"layout_similarity": 0.7}) == pytest.approx(0.7)


def test_layout_similarity_iou_mean():
    assert layout_similarity({"region_iou": [0.8, 0.6, 1.0]}) == pytest.approx(0.8)


def test_layout_similarity_tree_edit_distance():
    tree = {"type": "page", "children": [{"type": "header"}, {"type": "body"}]}
    # 동일 트리 → 유사도 1.0
    assert layout_similarity(
        {"original_layout_tree": tree, "pattern_layout_tree": tree}
    ) == pytest.approx(1.0)


def test_color_signature_cosine():
    h = [0.0, 1.0, 0.0, 2.0]
    assert color_signature(
        {"original_color_hist": h, "pattern_color_hist": h}
    ) == pytest.approx(1.0)


def test_structure_fingerprint_default_zero():
    assert structure_fingerprint({}) == 0.0


# ----------------------------------------------------------------------------
# 등급 경계 (스파이크 §3)
# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "score,expected",
    [
        (0.00, ReuseRisk.low),
        (0.30, ReuseRisk.low),
        (0.3001, ReuseRisk.medium),
        (0.60, ReuseRisk.medium),
        (0.6001, ReuseRisk.high),
        (0.80, ReuseRisk.high),
        (0.8001, ReuseRisk.blocked),
        (1.00, ReuseRisk.blocked),
    ],
)
def test_grade_boundaries(score, expected):
    assert score_to_risk(score) == expected


def test_grade_thresholds_constant_shape():
    # 보정 대비 상수 분리 확인.
    assert [g for _, g in GRADE_THRESHOLDS] == [
        ReuseRisk.low,
        ReuseRisk.medium,
        ReuseRisk.high,
        ReuseRisk.blocked,
    ]


# ----------------------------------------------------------------------------
# 가중 결합식 정확성 (스파이크 §2)
# ----------------------------------------------------------------------------
def test_weighted_combination_exact():
    feat = {
        "layout_similarity": 0.4,
        "color_signature": 0.2,
        "structure_fingerprint": 0.6,
        "brand_risk": 0.5,
    }
    raw = (
        0.30 * 0.4
        + 0.20 * 0.2
        + 0.25 * 0.6
        + 0.15 * 0.5
        + 0.10 * max(0.4, 0.2)
    )
    out = compute_reuse_risk(feat)
    assert out["reuse_score"] == pytest.approx(round(raw, 6))
    assert out["reuse_hardrule"] is None


def test_weights_sum_invariant():
    # 0.30+0.20+0.25+0.15+0.10 = 1.0 (max-bias 포함) → 모든 sub=1 이면 raw=1.0
    total = sum(WEIGHTS.values())
    assert total == pytest.approx(1.0)
    out = compute_reuse_risk(
        {
            "layout_similarity": 1.0,
            "color_signature": 1.0,
            "structure_fingerprint": 1.0,
            "brand_risk": 1.0,
        }
    )
    assert out["reuse_score"] == pytest.approx(1.0)


def test_max_bias_strong_signal_dominates():
    # 한 신호만 강함 — max-bias 가 끌어올림.
    weak = compute_reuse_risk(
        {"layout_similarity": 0.0, "color_signature": 0.0,
         "structure_fingerprint": 0.0, "brand_risk": 0.0}
    )["reuse_score"]
    strong_layout = compute_reuse_risk(
        {"layout_similarity": 0.9, "color_signature": 0.0,
         "structure_fingerprint": 0.0, "brand_risk": 0.0}
    )["reuse_score"]
    # layout=0.9 → 0.30*0.9 + 0.10*0.9 = 0.36
    assert strong_layout == pytest.approx(0.36)
    assert strong_layout > weak


def test_brand_risk_default_when_missing():
    out = compute_reuse_risk({"layout_similarity": 0.0, "color_signature": 0.0,
                              "structure_fingerprint": 0.0})
    # brand_risk 기본 0.5 → 0.15*0.5 = 0.075
    assert out["subscores"]["brand_risk"] == pytest.approx(0.5)
    assert out["reuse_score"] == pytest.approx(0.075)


def test_brand_risk_lookup_adapter():
    out = compute_reuse_risk(
        {"domain": "nike.com", "layout_similarity": 0.0,
         "color_signature": 0.0, "structure_fingerprint": 0.0},
        brand_risk_lookup=lambda d: 0.9 if d == "nike.com" else 0.1,
    )
    assert out["subscores"]["brand_risk"] == pytest.approx(0.9)


# ----------------------------------------------------------------------------
# 하드룰 (스파이크 §2)
# ----------------------------------------------------------------------------
def test_hardrule_text_leak_blocks():
    out = compute_reuse_risk(
        {
            "original_text": "our exclusive premium membership unlocks special benefits for loyal customers",
            "pattern_text": "our exclusive premium membership unlocks special benefits for loyal customers",
        }
    )
    assert out["reuse_risk"] == ReuseRisk.blocked
    assert out["reuse_hardrule"] == "text_overlap"
    assert out["reuse_score"] == pytest.approx(0.95)


def test_hardrule_artwork_floor():
    out = compute_reuse_risk(
        {"unique_artwork_detected": True, "layout_similarity": 0.1}
    )
    assert out["reuse_score"] >= HARDRULE_ARTWORK_FLOOR
    assert out["reuse_risk"] in (ReuseRisk.high, ReuseRisk.blocked)
    assert out["reuse_hardrule"] == "unique_artwork"


def test_hardrule_logo_face_floor():
    out_logo = compute_reuse_risk({"logo_detected": True})
    assert out_logo["reuse_score"] >= HARDRULE_LOGO_FACE_FLOOR
    assert out_logo["reuse_risk"] in (ReuseRisk.high, ReuseRisk.blocked)
    assert out_logo["reuse_hardrule"] == "logo_or_face"

    out_face = compute_reuse_risk({"face_detected": True})
    assert out_face["reuse_score"] >= HARDRULE_LOGO_FACE_FLOOR


def test_hardrule_text_leak_priority_over_floor():
    # 원문 누출이 로고보다 우선 → blocked 0.95.
    out = compute_reuse_risk(
        {
            "original_text": "alpha beta gamma delta epsilon zeta eta theta iota",
            "pattern_text": "alpha beta gamma delta epsilon zeta eta theta iota",
            "logo_detected": True,
        }
    )
    assert out["reuse_hardrule"] == "text_overlap"
    assert out["reuse_score"] == pytest.approx(0.95)


def test_floor_does_not_lower_high_raw():
    # raw 가 floor 보다 높으면 raw 유지(max).
    out = compute_reuse_risk(
        {"logo_detected": True, "layout_similarity": 1.0,
         "color_signature": 1.0, "structure_fingerprint": 1.0, "brand_risk": 1.0}
    )
    assert out["reuse_score"] == pytest.approx(1.0)


# ----------------------------------------------------------------------------
# Golden Set (스파이크 §4) — recall 100% 검증
# ----------------------------------------------------------------------------
def _golden_set():
    """라벨된 고정 샘플. (label, feature, 허용 등급 집합)."""
    return [
        # safe-structural → low: 추상 구조만, 약한 신호.
        (
            "safe-structural",
            {"layout_similarity": 0.2, "color_signature": 0.1,
             "structure_fingerprint": 0.2, "brand_risk": 0.1},
            {ReuseRisk.low},
        ),
        # brand-clone → high/blocked: 강브랜드 layout+color 근접.
        (
            "brand-clone",
            {"layout_similarity": 0.95, "color_signature": 0.92,
             "structure_fingerprint": 0.9, "brand_risk": 0.9},
            {ReuseRisk.high, ReuseRisk.blocked},
        ),
        # logo → ≥high (하드룰).
        (
            "logo",
            {"logo_detected": True, "layout_similarity": 0.1},
            {ReuseRisk.high, ReuseRisk.blocked},
        ),
        # face → ≥high (하드룰).
        (
            "face",
            {"face_detected": True, "layout_similarity": 0.1},
            {ReuseRisk.high, ReuseRisk.blocked},
        ),
        # text-leak → blocked (하드룰).
        (
            "text-leak",
            {"original_text": "this exact original sentence must not survive in the pattern output",
             "pattern_text": "this exact original sentence must not survive in the pattern output"},
            {ReuseRisk.blocked},
        ),
        # public-doc → low~medium.
        (
            "public-doc",
            {"layout_similarity": 0.3, "color_signature": 0.3,
             "structure_fingerprint": 0.4, "brand_risk": 0.1},
            {ReuseRisk.low, ReuseRisk.medium},
        ),
    ]


@pytest.mark.parametrize("label,feature,allowed", _golden_set())
def test_golden_set_grades(label, feature, allowed):
    out = compute_reuse_risk(feature)
    assert out["reuse_risk"] in allowed, (
        f"{label}: got {out['reuse_risk']} score={out['reuse_score']}, expected {allowed}"
    )


def test_recall_100_percent_on_dangerous_labels():
    """brand-clone / text-leak / logo / face 는 절대 low/medium 으로 새지 않아야 함.

    이 recall 이 100% 가 아니면 법적 사고 → 실패 케이스 0건 보장.
    """
    dangerous = {"brand-clone", "logo", "face", "text-leak"}
    misses = []
    for label, feature, _allowed in _golden_set():
        if label not in dangerous:
            continue
        risk = compute_reuse_risk(feature)["reuse_risk"]
        if risk in (ReuseRisk.low, ReuseRisk.medium):
            misses.append((label, risk))
    assert misses == [], f"recall<100%: leaked dangerous patterns as safe: {misses}"


def test_result_contract_keys():
    out = compute_reuse_risk({"layout_similarity": 0.5})
    assert set(out.keys()) == {"reuse_score", "reuse_risk", "reuse_hardrule", "subscores"}
    sub = out["subscores"]
    for k in ("text_overlap", "layout_similarity", "color_signature",
              "structure_fingerprint", "brand_risk", "logo", "face", "unique_artwork"):
        assert k in sub
    assert isinstance(out["reuse_risk"], ReuseRisk)
    assert 0.0 <= out["reuse_score"] <= 1.0
