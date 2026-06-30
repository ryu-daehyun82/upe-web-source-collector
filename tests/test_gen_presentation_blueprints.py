import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from gen_presentation_blueprints import build_blueprints, build_summary, _VISUAL_REGIONS  # noqa: E402


def test_blueprints_built():
    bps = build_blueprints()
    assert len(bps) >= 15
    ids = [b["blueprint_id"] for b in bps]
    assert len(set(ids)) == len(ids)  # 고유 id


def test_region_ratios_normalized():
    for b in build_blueprints():
        total = sum(b["region_ratios"].values())
        assert abs(total - 1.0) < 0.001, f"{b['blueprint_id']} ratios sum={total}"


def test_required_keys_and_types():
    for b in build_blueprints():
        for k in ("blueprint_id", "intent", "genre", "layout_type", "section_order",
                  "region_ratios", "visual_area_share", "text_visual_ratio", "when_to_use"):
            assert k in b, f"{b['blueprint_id']} missing {k}"
        assert b["layout_type"] == "slide"
        assert isinstance(b["genre"], list) and b["genre"]
        # section_order 의 영역이 region_ratios 키에 모두 존재
        assert set(b["section_order"]) <= set(b["region_ratios"])
        tv = b["text_visual_ratio"]
        assert abs(tv["text"] + tv["visual"] - 1.0) < 0.001


def test_visual_area_share_matches_visual_regions():
    for b in build_blueprints():
        expected = round(sum(r for c, r in b["region_ratios"].items() if c in _VISUAL_REGIONS), 4)
        assert b["visual_area_share"] == expected


def test_summary_genre_index_and_intents():
    bps = build_blueprints()
    s = build_summary(bps)
    assert s["total_blueprints"] == len(bps)
    # 4개 타깃 장르 모두 인덱스에 존재
    for g in ("proposal", "business_plan", "company_intro", "strategy_report"):
        assert g in s["genre_index"] and s["genre_index"][g]
    assert "executive_summary" in s["intents"]
    assert "architecture" in s["intents"]
    assert 0.0 <= s["avg_visual_area_share"] <= 1.0


def test_architecture_is_visual_heavy():
    bps = build_blueprints()
    arch = next(b for b in bps if b["intent"] == "architecture")
    assert arch["text_visual_ratio"]["visual"] >= 0.7
    assert arch["visual_area_share"] >= 0.6