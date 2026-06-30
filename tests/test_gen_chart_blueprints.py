import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from gen_chart_blueprints import (  # noqa: E402
    build_chart_blueprints, build_summary, load_stats, _CHART_TYPE_STATS, _CHART_INTENTS,
)


def test_builds_all_intents():
    bps = build_chart_blueprints()
    assert len(bps) == len(_CHART_INTENTS)
    ids = [b["blueprint_id"] for b in bps]
    assert len(set(ids)) == len(ids)
    intents = {b["intent"] for b in bps}
    for must in ("comparison", "ranking", "trend", "composition", "dual_metric"):
        assert must in intents


def test_structure_grounded_in_stats():
    bps = build_chart_blueprints()
    trend = next(b for b in bps if b["intent"] == "trend")
    assert trend["recommended_chart_type"] == "line"
    # line 실측 avg_category 5.79 → round 6
    assert trend["structure"]["typical_category_count"] == 6
    assert trend["structure"]["typical_color_count"] == 3   # line avg_color 3.0
    assert trend["available_in_data"] is True
    assert trend["empirical"]["n"] == _CHART_TYPE_STATS["line"]["n"]


def test_pie_small_categories():
    bps = build_chart_blueprints()
    comp = next(b for b in bps if b["intent"] == "composition")
    assert comp["recommended_chart_type"] == "pie"
    assert comp["structure"]["max_categories_guideline"] == 6


def test_beyond_data_intents_marked():
    bps = build_chart_blueprints()
    for intent in ("correlation", "distribution"):
        b = next(x for x in bps if x["intent"] == intent)
        assert b["available_in_data"] is False
        assert b["empirical"] is None


def test_series_from_intent():
    bps = build_chart_blueprints()
    # composition_over_time(누적 막대)는 다계열
    cot = next(b for b in bps if b["intent"] == "composition_over_time")
    assert cot["recommended_chart_type"] == "bar"
    assert cot["structure"]["typical_series_count"] == 3


def test_load_stats_recompute(tmp_path):
    import json
    p = tmp_path / "patterns.jsonl"
    rows = [
        {"feature": {"chart_type": "bar", "category_count": 4, "series_count": 1, "color_count": 2,
                     "legend_present": True, "data_labels_present": True}},
        {"feature": {"chart_type": "bar", "category_count": 6, "series_count": 1, "color_count": 2,
                     "legend_present": True, "data_labels_present": True}},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    stats = load_stats(str(p))
    assert stats["bar"]["n"] == 2
    assert stats["bar"]["avg_category"] == 5.0   # (4+6)/2


def test_summary_structure():
    bps = build_chart_blueprints()
    s = build_summary(bps, _CHART_TYPE_STATS)
    assert s["total_chart_blueprints"] == len(bps)
    assert "by_recommended_chart_type" in s
    assert "empirical_stats_by_type" in s
    for g in ("business_plan", "strategy_report"):
        assert g in s["genre_index"]