"""차트 패턴 파서 (AIHub 문서이해 기반 시각요소 생성 차트 메타 → raw_feature).

차트 메타데이터에서 **구조/유형/개수/유무만** 추출한다. 원본 라벨/값 텍스트는 미추출
→ original_text="", pattern_text=""(누출 없음). DocLayNet(문서 레이아웃)과 별개의
패턴 차원(차트 구성)을 UPE 거버넌스에 공급. 라이선스는 호출부에서 conditional_approved
(비상업·약관 수락)로 태깅. 순수 stdlib.
"""
from __future__ import annotations

# 차트 유형 정규화(별칭 → 표준)
_CHART_TYPE_ALIASES: dict[str, str] = {
    "vertical bar": "bar", "horizontal bar": "bar", "bar": "bar", "column": "bar",
    "line": "line", "pie": "pie", "donut": "pie", "doughnut": "pie",
    "scatter": "scatter", "area": "area", "mixed": "mixed",
}


def _normalize_chart_type(raw) -> str:
    """str 이면 소문자 strip 후 별칭 매핑(없으면 그 소문자값), 그 외/None 이면 'unknown'."""
    if not isinstance(raw, str):
        return "unknown"
    key = raw.strip().lower()
    if not key:
        return "unknown"
    return _CHART_TYPE_ALIASES.get(key, key)


def _count(v) -> int:
    """list/tuple/set/dict 면 len, 그 외/None 이면 0."""
    if isinstance(v, (list, tuple, set, dict)):
        return len(v)
    return 0


def _first(meta: dict, keys: tuple[str, ...]):
    """meta 에서 keys 중 처음 존재하는 값 반환(없으면 None)."""
    for k in keys:
        if k in meta:
            return meta[k]
    return None


def parse_chart_metadata(meta: dict | None, *, url: str | None = None) -> dict:
    """차트 메타데이터 dict → raw_feature. 필드명 변형에 관대(여러 키 폴백).

    구조만 추출(원본 라벨/값 텍스트 미추출): chart_type / category_count / series_count /
    color_count / legend_present / data_labels_present. layout_type 은 chart 구조가 있으면 "chart".
    """
    if not meta or not isinstance(meta, dict):
        result: dict = {
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
        return result

    chart_type = _normalize_chart_type(_first(meta, ("chart_type", "type", "chartType")))
    category_count = _count(_first(meta, ("categories", "labels", "x", "x_categories")))
    series_count = _count(_first(meta, ("series", "datasets", "data", "y_series")))
    color_count = _count(_first(meta, ("colors", "palette", "color_palette")))
    legend_present = bool(_first(meta, ("legend",)))
    data_labels_present = bool(_first(meta, ("data_labels", "dataLabels", "show_data_labels")))

    has_structure = (
        chart_type != "unknown"
        or category_count
        or series_count
        or color_count
        or legend_present
        or data_labels_present
    )

    result = {
        "layout_type": "chart" if has_structure else "unknown",
        "chart_type": chart_type,
        "category_count": category_count,
        "series_count": series_count,
        "color_count": color_count,
        "legend_present": legend_present,
        "data_labels_present": data_labels_present,
        "original_text": "",
        "pattern_text": "",
    }
    if url is not None:
        result["source_url"] = url
    return result


def parse_chart_dataset(items: list[dict], *, url: str | None = None) -> list[dict]:
    """차트 메타 리스트 → raw_feature 리스트. item.get('metadata')가 dict 면 그걸, 아니면 item 사용."""
    results: list[dict] = []
    for item in items:
        meta = item.get("metadata") if isinstance(item, dict) and isinstance(item.get("metadata"), dict) else item
        results.append(parse_chart_metadata(meta, url=url))
    return results