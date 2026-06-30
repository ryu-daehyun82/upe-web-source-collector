"""DocLayNet COCO 파서 어댑터 (§8.4 확장).

DocLayNet COCO 어노테이션(11클래스 bbox)을 거버넌스(run_pattern_governance)용
raw_feature dict 로 변환한다. 원본 텍스트/픽셀은 미포함 → original_text="", pattern_text=""
(누출 없음). abstraction_guard 허용키(section_order/region_ratios/table_structure/card_count/
layout_type) 위주. CDLA-Permissive-1.0 라이선스 데이터(license_status=allowed). stdlib 만.
"""
from __future__ import annotations

from collections import defaultdict

# DocLayNet 11 클래스 (COCO category_id → name 폴백)
_CATEGORY_NAMES: dict[int, str] = {
    1: "Caption", 2: "Footnote", 3: "Formula", 4: "List-item", 5: "Page-footer",
    6: "Page-header", 7: "Picture", 8: "Section-header", 9: "Table", 10: "Text", 11: "Title",
}


def parse_doclaynet_page(
    width: float,
    height: float,
    regions: list[dict],
    *,
    url: str | None = None,
) -> dict:
    """단일 페이지 → raw_feature.

    regions: [{"category": str, "bbox": [x, y, w, h]}, ...] (COCO bbox = x,y,width,height).
    반환: layout_type / section_order(읽기순서) / region_ratios(면적비율) /
    table_structure{tables,rows} / card_count / original_text="" / pattern_text="". url→source_url.
    """
    if not regions or width <= 0 or height <= 0:
        result: dict = {
            "layout_type": "unknown",
            "section_order": [],
            "region_ratios": {},
            "table_structure": {"tables": 0, "rows": 0},
            "card_count": 0,
            "original_text": "",
            "pattern_text": "",
        }
        if url is not None:
            result["source_url"] = url
        return result

    page_area = width * height
    category_areas: dict[str, float] = defaultdict(float)
    valid_regions = [r for r in regions if len(r.get("bbox", [])) >= 4]
    table_count = 0
    card_count = 0

    for region in valid_regions:
        bbox = region["bbox"]
        cat = region["category"]
        category_areas[cat] += bbox[2] * bbox[3]
        if cat == "Table":
            table_count += 1
        if cat == "List-item":
            card_count += 1

    region_ratios = {
        cat: round(max(0.0, min(1.0, area_sum / page_area)), 4)
        for cat, area_sum in category_areas.items()
    }

    sorted_regions = sorted(valid_regions, key=lambda r: (r["bbox"][1], r["bbox"][0]))
    section_order = [r["category"] for r in sorted_regions]

    result = {
        "layout_type": "document",
        "section_order": section_order,
        "region_ratios": region_ratios,
        "table_structure": {"tables": table_count, "rows": 0},
        "card_count": card_count,
        "original_text": "",
        "pattern_text": "",
    }
    if url is not None:
        result["source_url"] = url
    return result


def parse_doclaynet_coco(coco: dict, *, url: str | None = None) -> list[dict]:
    """전체 COCO dict → 페이지별 raw_feature 리스트.

    coco: {"images":[{id,width,height}], "annotations":[{image_id,category_id,bbox}],
    "categories":[{id,name}]}. categories 비면 _CATEGORY_NAMES 폴백.
    """
    cats: dict[int, str] = {c["id"]: c["name"] for c in coco.get("categories", [])}
    if not cats:
        cats = _CATEGORY_NAMES

    ann_by_image: dict[int, list[dict]] = defaultdict(list)
    for ann in coco.get("annotations", []):
        ann_by_image[ann["image_id"]].append({
            "category": cats.get(ann["category_id"], str(ann["category_id"])),
            "bbox": ann["bbox"],
        })

    results: list[dict] = []
    for img in coco.get("images", []):
        regions = ann_by_image.get(img["id"], [])
        results.append(parse_doclaynet_page(img["width"], img["height"], regions, url=url))
    return results