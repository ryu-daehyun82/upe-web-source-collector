from app.workers.chart_pattern_parser import (
    parse_chart_metadata, parse_chart_dataset, _normalize_chart_type, _count,
)
from app.pattern.abstraction_guard import guard
from app.pipeline import run_pattern_governance


# ── 정규화/헬퍼 ───────────────────────────────────────────────────

def test_normalize_chart_type():
    assert _normalize_chart_type("vertical bar") == "bar"
    assert _normalize_chart_type("PIE") == "pie"
    assert _normalize_chart_type("Doughnut") == "pie"
    assert _normalize_chart_type("radar") == "radar"   # 미지 → 소문자 그대로
    assert _normalize_chart_type(None) == "unknown"
    assert _normalize_chart_type(123) == "unknown"
    assert _normalize_chart_type("  ") == "unknown"


def test_count_helper():
    assert _count([1, 2, 3]) == 3
    assert _count({"a": 1}) == 1
    assert _count(None) == 0
    assert _count("x") == 0


# ── parse_chart_metadata ─────────────────────────────────────────

def test_full_metadata():
    meta = {
        "chart_type": "vertical bar",
        "categories": ["a", "b", "c"],
        "series": [{"x": 1}, {"x": 2}],
        "colors": ["#fff", "#000"],
        "legend": ["s1", "s2"],
        "data_labels": True,
    }
    r = parse_chart_metadata(meta)
    assert r["layout_type"] == "chart"
    assert r["chart_type"] == "bar"
    assert r["category_count"] == 3
    assert r["series_count"] == 2
    assert r["color_count"] == 2
    assert r["legend_present"] is True
    assert r["data_labels_present"] is True
    assert r["original_text"] == "" and r["pattern_text"] == ""


def test_empty_and_none():
    for m in (None, {}):
        r = parse_chart_metadata(m)
        assert r["layout_type"] == "unknown"
        assert r["chart_type"] == "unknown"
        assert r["category_count"] == 0
        assert r["series_count"] == 0
        assert r["legend_present"] is False


def test_unknown_type_still_chart():
    r = parse_chart_metadata({"type": "radar", "categories": [1, 2]})
    assert r["chart_type"] == "radar"
    assert r["layout_type"] == "chart"


def test_no_legend():
    r = parse_chart_metadata({"type": "line", "categories": [1, 2, 3]})
    assert r["legend_present"] is False
    assert r["data_labels_present"] is False


def test_url_included():
    r = parse_chart_metadata({"type": "pie"}, url="https://ex.com/chart#1")
    assert r["source_url"] == "https://ex.com/chart#1"
    assert "source_url" not in parse_chart_metadata({"type": "pie"})


def test_no_original_labels_stored():
    # 원본 카테고리/값 텍스트는 결과 어디에도 저장 안 됨 — 개수만
    meta = {"type": "bar", "categories": ["민감라벨A", "민감라벨B"], "title": "비밀 제목"}
    r = parse_chart_metadata(meta)
    dumped = str(r)
    assert "민감라벨" not in dumped
    assert "비밀 제목" not in dumped
    assert r["category_count"] == 2


def test_alias_fields():
    # 폴백 필드명: chartType / labels / datasets / palette / dataLabels
    meta = {"chartType": "column", "labels": [1, 2], "datasets": [1], "palette": ["a"], "dataLabels": 1}
    r = parse_chart_metadata(meta)
    assert r["chart_type"] == "bar"
    assert r["category_count"] == 2
    assert r["series_count"] == 1
    assert r["color_count"] == 1
    assert r["data_labels_present"] is True


# ── parse_chart_dataset ──────────────────────────────────────────

def test_dataset_metadata_wrapper_and_flat():
    items = [{"metadata": {"type": "line", "categories": [1, 2]}}, {"type": "pie"}]
    out = parse_chart_dataset(items)
    assert len(out) == 2
    assert out[0]["chart_type"] == "line" and out[0]["category_count"] == 2
    assert out[1]["chart_type"] == "pie"


# ── abstraction_guard / governance 통합 ──────────────────────────

def test_chart_keys_survive_abstraction_guard():
    # 차트 구조 키가 화이트리스트로 통과(원문/제거대상이 아님)
    feat = parse_chart_metadata({"type": "bar", "categories": [1, 2], "colors": ["a", "b"]})
    abstracted, removed = guard(feat)
    assert abstracted["chart_type"] == "bar"
    assert abstracted["category_count"] == 2
    assert abstracted["layout_type"] == "chart"
    assert "color_count" in abstracted


def test_chart_feature_passes_governance_low():
    feat = parse_chart_metadata({"type": "bar", "categories": [1, 2, 3], "colors": ["a", "b"], "legend": ["x"]})
    d = run_pattern_governance(feat)
    assert d.operational is True
    assert d.original_reuse_risk == "low"
    assert d.pattern_status == "approved"
    assert "original_text" not in d.abstracted_feature