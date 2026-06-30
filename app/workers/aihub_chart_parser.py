"""AIHub "문서 이해 기반 시각요소 생성" 차트 라벨 → 거버넌스용 raw_feature.

실제 스키마(annotations[0]): chart_type(한국어 선형/막대형/원형/혼합형) · category(list) ·
legend(list) · data_label(list) · unit. visualize_code(matplotlib)에서 색 팔레트 추출.
원본 텍스트(text.description/title/category 라벨/qa_reasoning)는 **미추출** →
original_text="", pattern_text="". 구조/유형/개수/유무만(누출 없음). 순수 stdlib.

비상업 개인프로젝트 = license_status conditional_approved(약관 수락·비상업).
"""
from __future__ import annotations

import re

_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")


def _kr_chart_type(s) -> str:
    """한국어/영문 차트유형 → 표준(bar/line/pie/mixed/scatter/area). 미상은 'unknown'."""
    if not isinstance(s, str):
        return "unknown"
    t = s.strip()
    if not t:
        return "unknown"
    if "막대" in t or "bar" in t.lower() or "column" in t.lower():
        return "bar"
    if "선" in t or "line" in t.lower():
        return "line"
    if "원" in t or "파이" in t or "pie" in t.lower() or "donut" in t.lower():
        return "pie"
    if "혼합" in t or "mixed" in t.lower():
        return "mixed"
    if "산점" in t or "scatter" in t.lower():
        return "scatter"
    if "영역" in t or "area" in t.lower():
        return "area"
    return t.lower()


def _count(v) -> int:
    return len(v) if isinstance(v, (list, tuple, set, dict)) else 0


def parse_aihub_chart(d: dict | None, *, url: str | None = None) -> dict:
    """AIHub 차트 라벨 JSON → raw_feature(구조만). 빈/비정상 → layout_type 'unknown'."""
    result = {
        "layout_type": "unknown",
        "chart_type": "unknown",
        "category_count": 0,
        "series_count": 0,
        "color_count": 0,
        "legend_present": False,
        "data_labels_present": False,
        "original_text": "",
        "pattern_text": "",
    }
    if url is not None:
        result["source_url"] = url
    if not d or not isinstance(d, dict):
        return result

    anns = d.get("annotations")
    ann = anns[0] if isinstance(anns, list) and anns else (anns if isinstance(anns, dict) else {})

    chart_type = _kr_chart_type(ann.get("chart_type"))
    legend = ann.get("legend") or []
    category_count = _count(ann.get("category"))
    series_count = _count(legend)  # legend 항목 ≈ 시리즈 수
    color_count = len(set(_HEX_RE.findall(d.get("visualize_code", "") or "")))
    legend_present = bool(legend)
    data_labels_present = bool(ann.get("data_label"))

    has_structure = chart_type != "unknown" or category_count or series_count or color_count
    result.update({
        "layout_type": "chart" if has_structure else "unknown",
        "chart_type": chart_type,
        "category_count": category_count,
        "series_count": series_count,
        "color_count": color_count,
        "legend_present": legend_present,
        "data_labels_present": data_labels_present,
    })
    return result