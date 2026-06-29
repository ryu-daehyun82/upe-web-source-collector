"""Abstraction Guard 테스트 (설계서 §9.2 / §9.3).

제거 대상 솎아내기 + 추상화 허용 대상만 통과 + raw_text/image_pixels 제거 확인.
"""
from app.pattern.abstraction_guard import (
    ABSTRACTION_ALLOWED,
    REMOVAL_TARGETS,
    guard,
    is_fully_abstracted,
)


def test_removes_raw_text():
    feat = {"raw_text": "long original article body...", "section_order": ["a", "b"]}
    abstracted, removed = guard(feat)
    assert "raw_text" not in abstracted
    assert abstracted["raw_text_removed"] is True
    assert "long_form_original_text" in removed
    assert abstracted["section_order"] == ["a", "b"]


def test_removes_image_pixels():
    feat = {"image_pixels": b"...", "layout_type": "grid"}
    abstracted, removed = guard(feat)
    assert "image_pixels" not in abstracted
    assert abstracted["image_pixels_removed"] is True
    assert "image_pixels" in removed


def test_removes_logo_face_signature_artwork():
    feat = {
        "logo_asset": "...",
        "face_crops": ["..."],
        "signature": "...",
        "unique_artwork": "...",
        "design_replica": "...",
        "card_count": 3,
    }
    abstracted, removed = guard(feat)
    for key in ("logo_asset", "face_crops", "signature", "unique_artwork", "design_replica"):
        assert key not in abstracted
    assert abstracted["card_count"] == 3
    assert abstracted["image_pixels_removed"] is True
    # 사유 라벨 매핑 확인.
    assert "logo" in removed
    assert "person_face" in removed
    assert "signature" in removed
    assert "original_illustration" in removed
    assert "design_replica" in removed


def test_allows_only_whitelisted_structure():
    feat = {
        "section_order": [1, 2, 3],
        "region_ratios": [0.5, 0.5],
        "color_count": 4,
        "table_structure": {"rows": 3, "cols": 2},
        "slide_flow": ["intro", "body"],
        # 미상 키 — 보수적으로 제거.
        "mystery_field": "could be expression",
    }
    abstracted, removed = guard(feat)
    assert abstracted["section_order"] == [1, 2, 3]
    assert abstracted["color_count"] == 4
    assert "mystery_field" not in abstracted
    assert "unallowed:mystery_field" in removed


def test_leak_probe_separation():
    # original_text/pattern_text 는 본문에서 빠지고 _leak_probe 로 분리 보존.
    feat = {"original_text": "abc def", "pattern_text": "abc def", "color_count": 2}
    abstracted, _ = guard(feat)
    assert "original_text" not in abstracted
    assert "pattern_text" not in abstracted
    assert abstracted["_leak_probe"]["original_text"] == "abc def"
    assert abstracted["_leak_probe"]["pattern_text"] == "abc def"


def test_is_fully_abstracted_true_after_guard():
    feat = {"raw_text": "...", "image_pixels": b"...", "section_order": [1]}
    abstracted, _ = guard(feat)
    assert is_fully_abstracted(abstracted) is True


def test_is_fully_abstracted_false_on_residual():
    # 외부에서 잘못 조립해 제거 대상 키가 남은 경우 → False.
    bad = {"section_order": [1], "raw_text": "still here"}
    assert is_fully_abstracted(bad) is False


def test_no_overlap_between_removal_and_allowed():
    # 화이트리스트와 제거대상 키가 겹치면 안 됨(모순 방지).
    assert set(REMOVAL_TARGETS).isdisjoint(ABSTRACTION_ALLOWED)


def test_clean_feature_passes_through():
    feat = {"layout_similarity": 0.5, "color_signature": 0.3, "domain": "x.com"}
    abstracted, removed = guard(feat)
    assert abstracted["layout_similarity"] == 0.5
    assert abstracted["domain"] == "x.com"
    assert removed == []
    assert abstracted["raw_text_removed"] is False
    assert abstracted["image_pixels_removed"] is False
