from app.workers.aihub_chart_parser import parse_aihub_chart, _kr_chart_type
from app.pattern.abstraction_guard import guard
from app.pipeline import run_pattern_governance


def test_kr_chart_type():
    assert _kr_chart_type("선형") == "line"
    assert _kr_chart_type("가로 막대형") == "bar"
    assert _kr_chart_type("세로 막대형") == "bar"
    assert _kr_chart_type("원형") == "pie"
    assert _kr_chart_type("파이형") == "pie"
    assert _kr_chart_type("혼합형") == "mixed"
    assert _kr_chart_type("line") == "line"
    assert _kr_chart_type(None) == "unknown"
    assert _kr_chart_type("  ") == "unknown"


def _sample(chart_type="선형", cats=5, legend=1, data_label=True, code="c='#1f77b4'; d='#ff7f0e'"):
    return {
        "text": {"text_id": 1, "description": "민감한 원문 설명 텍스트"},
        "metadata": {"license_type": "제1유형"},
        "annotations": [{
            "chart_type": chart_type, "chart_subtype": "기본형",
            "title": "비밀 제목",
            "category": list(range(cats)),
            "legend": list(range(legend)),
            "unit": "%",
            "data_label": [1] if data_label else [],
        }],
        "imgs": {"img_width": 1973, "img_height": 1292},
        "visualize_code": code,
        "qa_reasoning": [{"question": "q", "answer": "a"}],
    }


def test_parse_real_schema():
    feat = parse_aihub_chart(_sample("선형", cats=5, legend=1))
    assert feat["layout_type"] == "chart"
    assert feat["chart_type"] == "line"
    assert feat["category_count"] == 5
    assert feat["series_count"] == 1
    assert feat["color_count"] == 2          # #1f77b4, #ff7f0e
    assert feat["legend_present"] is True
    assert feat["data_labels_present"] is True
    assert feat["original_text"] == "" and feat["pattern_text"] == ""


def test_no_original_text_or_title_leaked():
    feat = parse_aihub_chart(_sample())
    dumped = str(feat)
    assert "민감한 원문" not in dumped
    assert "비밀 제목" not in dumped
    assert "q" not in [feat.get(k) for k in feat]  # qa 미포함


def test_empty_and_none():
    for d in (None, {}, {"annotations": []}):
        feat = parse_aihub_chart(d)
        assert feat["layout_type"] == "unknown"
        assert feat["chart_type"] == "unknown"
        assert feat["category_count"] == 0


def test_bar_pie_mixed_types():
    assert parse_aihub_chart(_sample("세로 막대형"))["chart_type"] == "bar"
    assert parse_aihub_chart(_sample("원형"))["chart_type"] == "pie"
    assert parse_aihub_chart(_sample("혼합형"))["chart_type"] == "mixed"


def test_url_included():
    feat = parse_aihub_chart(_sample(), url="aihub:71957/3342")
    assert feat["source_url"] == "aihub:71957/3342"


def test_passes_abstraction_guard_and_governance():
    feat = parse_aihub_chart(_sample("세로 막대형", cats=4, legend=2))
    abstracted, _ = guard(feat)
    # 차트 구조 키가 화이트리스트로 통과
    assert abstracted["chart_type"] == "bar"
    assert abstracted["category_count"] == 4
    assert abstracted["color_count"] == 2
    d = run_pattern_governance(feat)
    assert d.operational is True
    assert d.original_reuse_risk == "low"
    assert "original_text" not in d.abstracted_feature