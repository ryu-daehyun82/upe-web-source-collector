import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from gen_presentation_blueprints import build_blueprints, _VISUAL_REGIONS  # noqa: E402
from calibrate_blueprints import (  # noqa: E402
    calibrate_blueprint, load_priors, build_summary,
    _DOCLAYNET_PRIORS, _REGION_TO_DOCNET,
)


def test_load_priors_fallback():
    pri = load_priors(None)
    assert pri == _DOCLAYNET_PRIORS
    assert pri["Text"] == 0.251 and pri["Table"] == 0.2905


def test_calibrate_keeps_sum_one_and_metadata():
    pri = load_priors(None)
    for b in build_blueprints():
        c = calibrate_blueprint(b, pri, alpha=0.4)
        assert abs(sum(c["region_ratios"].values()) - 1.0) < 0.001
        assert "region_ratios_design" in c
        assert c["calibration"] == {"from": "doclaynet", "alpha": 0.4}
        # design 보존(원본)
        assert c["region_ratios_design"] == b["region_ratios"]


def test_visual_regions_not_blended():
    # alpha=1 이어도 시각영역은 prior 블렌딩 대상 아님(렌놈만). 비-시각 매핑영역만 prior로 이동.
    pri = load_priors(None)
    arch = next(b for b in build_blueprints() if b["intent"] == "architecture")
    c = calibrate_blueprint(arch, pri, alpha=1.0)
    # Diagram(visual)은 prior에 없고 미보정 → 렌놈 후에도 최대 비중 유지
    assert c["region_ratios"]["Diagram"] == max(c["region_ratios"].values())


def test_text_region_moves_toward_prior():
    # Body(=Text prior 0.251)가 design보다 작으면 보정 후 커지고, 크면 작아진다.
    pri = load_priors(None)
    bp_small = {"blueprint_id": "x", "intent": "t", "genre": ["proposal"], "layout_type": "slide",
                "section_order": ["Body", "Visual"], "region_ratios": {"Body": 0.1, "Visual": 0.9},
                "visual_area_share": 0.9, "text_visual_ratio": {"text": 0.1, "visual": 0.9},
                "when_to_use": ""}
    c = calibrate_blueprint(bp_small, pri, alpha=0.5)
    # Body 0.1 → prior 0.251 쪽으로(블렌딩 0.5*0.1+0.5*0.251=0.1755) 후 렌놈 → design보다 큼
    assert c["region_ratios"]["Body"] > 0.1


def test_visual_share_increases_when_text_oversized():
    # 텍스트를 과하게 크게 설계한 경우 보정으로 시각비중이 오른다.
    pri = load_priors(None)
    bp = {"blueprint_id": "y", "intent": "t", "genre": ["proposal"], "layout_type": "slide",
          "section_order": ["Body", "Visual"], "region_ratios": {"Body": 0.6, "Visual": 0.4},
          "visual_area_share": 0.4, "text_visual_ratio": {"text": 0.6, "visual": 0.4},
          "when_to_use": ""}
    c = calibrate_blueprint(bp, pri, alpha=0.5)
    assert c["visual_area_share"] > 0.4   # Body 압축 → Visual 비중 상승


def test_summary_before_after():
    pri = load_priors(None)
    design = build_blueprints()
    cal = [calibrate_blueprint(b, pri, alpha=0.4) for b in design]
    s = build_summary(design, cal, alpha=0.4, priors=pri)
    assert s["total_blueprints"] == len(design)
    assert s["calibration"]["alpha"] == 0.4
    assert "avg_visual_area_share_before" in s and "avg_visual_area_share_after" in s
    for g in ("proposal", "business_plan", "company_intro", "strategy_report"):
        assert g in s["genre_index"]


def test_region_mapping_non_visual_only():
    # 매핑 테이블엔 시각영역이 없어야 한다(시각은 미보정 보장).
    for region in _REGION_TO_DOCNET:
        assert region not in _VISUAL_REGIONS