import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from export_patterns import _build_summary, _README  # noqa: E402


def _row(section_order, region_ratios, tables=0, ptype="document_layout"):
    return {
        "id": "x", "pattern_type": ptype, "license_status": "allowed",
        "original_reuse_risk": "low", "pattern_status": "approved",
        "feature": {
            "layout_type": "document",
            "section_order": section_order,
            "region_ratios": region_ratios,
            "table_structure": {"tables": tables, "rows": 0},
            "card_count": 0,
        },
    }


def test_summary_counts_and_distributions():
    rows = [
        _row(["Title", "Text"], {"Title": 0.1, "Text": 0.5}),
        _row(["Title", "Text"], {"Title": 0.3, "Text": 0.7}),
        _row(["Table"], {"Table": 0.4}, tables=1),
    ]
    s = _build_summary(rows)
    assert s["total_patterns"] == 3
    assert s["by_pattern_type"] == {"document_layout": 3}
    assert s["by_layout_type"] == {"document": 3}
    # 카테고리 빈도(등장 패턴 수)
    assert s["category_frequency"]["Title"] == 2
    assert s["category_frequency"]["Table"] == 1
    # 평균 영역비율
    assert s["avg_region_ratio_by_category"]["Title"] == 0.2   # (0.1+0.3)/2
    assert s["avg_region_ratio_by_category"]["Text"] == 0.6
    # 표 통계
    assert s["table_presence_rate"] == round(1 / 3, 4)
    assert s["avg_tables_when_present"] == 1.0


def test_top_section_order_sequences():
    rows = [
        _row(["Title", "Text"], {"Title": 0.1}),
        _row(["Title", "Text"], {"Title": 0.1}),
        _row(["Picture"], {"Picture": 0.5}),
    ]
    s = _build_summary(rows)
    top = s["top_section_order_sequences"]
    assert top[0] == {"section_order": ["Title", "Text"], "count": 2}


def test_empty_summary():
    s = _build_summary([])
    assert s["total_patterns"] == 0
    assert s["table_presence_rate"] == 0.0
    assert s["top_section_order_sequences"] == []


def test_readme_has_schema():
    assert "section_order" in _README
    assert "region_ratios" in _README


def test_zip_roundtrip(tmp_path):
    # _build_summary + 수동 zip(스크립트와 동일 구조)으로 zip 무결성 확인
    import json
    rows = [_row(["Title", "Text"], {"Title": 0.1, "Text": 0.5})]
    summary = _build_summary(rows)
    out = tmp_path / "export.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("patterns.jsonl", json.dumps(rows[0]) + "\n")
        zf.writestr("summary.json", json.dumps(summary))
        zf.writestr("README.md", _README)
    with zipfile.ZipFile(out) as zf:
        assert set(zf.namelist()) == {"patterns.jsonl", "summary.json", "README.md"}
        loaded = json.loads(zf.read("summary.json"))
        assert loaded["total_patterns"] == 1