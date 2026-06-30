from app.workers.doclaynet_parser import (
    parse_doclaynet_page, parse_doclaynet_coco, _CATEGORY_NAMES,
)
from app.pipeline import run_pattern_governance


def test_page_basic_layout():
    regions = [
        {"category": "Title", "bbox": [0, 0, 1000, 100]},
        {"category": "Text", "bbox": [0, 200, 1000, 500]},
        {"category": "Table", "bbox": [0, 800, 1000, 150]},
    ]
    r = parse_doclaynet_page(1000, 1000, regions)
    assert r["layout_type"] == "document"
    assert r["section_order"] == ["Title", "Text", "Table"]  # y 오름차순
    assert r["region_ratios"] == {"Title": 0.1, "Text": 0.5, "Table": 0.15}
    assert r["table_structure"] == {"tables": 1, "rows": 0}
    assert r["card_count"] == 0
    assert r["original_text"] == "" and r["pattern_text"] == ""


def test_reading_order_sort_by_y_then_x():
    regions = [
        {"category": "Text", "bbox": [500, 100, 100, 100]},
        {"category": "Title", "bbox": [0, 100, 100, 100]},   # 같은 y, x 작음 → 먼저
        {"category": "Footnote", "bbox": [0, 900, 100, 50]},
    ]
    r = parse_doclaynet_page(1000, 1000, regions)
    assert r["section_order"] == ["Title", "Text", "Footnote"]


def test_list_item_card_count():
    regions = [
        {"category": "List-item", "bbox": [0, 0, 100, 50]},
        {"category": "List-item", "bbox": [0, 60, 100, 50]},
        {"category": "Text", "bbox": [0, 200, 100, 50]},
    ]
    assert parse_doclaynet_page(1000, 1000, regions)["card_count"] == 2


def test_empty_regions_unknown():
    r = parse_doclaynet_page(1000, 1000, [])
    assert r["layout_type"] == "unknown"
    assert r["section_order"] == []
    assert r["region_ratios"] == {}
    assert r["table_structure"] == {"tables": 0, "rows": 0}


def test_zero_dimensions_unknown():
    regions = [{"category": "Title", "bbox": [0, 0, 100, 100]}]
    assert parse_doclaynet_page(0, 1000, regions)["layout_type"] == "unknown"


def test_short_bbox_skipped():
    regions = [
        {"category": "Title", "bbox": [0, 0, 1000, 100]},
        {"category": "Bad", "bbox": [0, 0, 100]},  # len 3 → 스킵
    ]
    r = parse_doclaynet_page(1000, 1000, regions)
    assert r["section_order"] == ["Title"]
    assert "Bad" not in r["region_ratios"]


def test_url_included():
    regions = [{"category": "Title", "bbox": [0, 0, 100, 100]}]
    r = parse_doclaynet_page(1000, 1000, regions, url="https://ex.com/doc#p1")
    assert r["source_url"] == "https://ex.com/doc#p1"
    assert "source_url" not in parse_doclaynet_page(1000, 1000, regions)


def test_coco_with_categories():
    coco = {
        "images": [{"id": 1, "width": 1000, "height": 1000}],
        "annotations": [{"image_id": 1, "category_id": 11, "bbox": [0, 0, 1000, 100]}],
        "categories": [{"id": 11, "name": "Title"}],
    }
    pages = parse_doclaynet_coco(coco)
    assert len(pages) == 1
    assert pages[0]["section_order"] == ["Title"]


def test_coco_category_fallback():
    # categories 비면 _CATEGORY_NAMES 폴백 (11 → Title)
    coco = {
        "images": [{"id": 1, "width": 1000, "height": 1000}],
        "annotations": [{"image_id": 1, "category_id": 11, "bbox": [0, 0, 1000, 100]}],
        "categories": [],
    }
    pages = parse_doclaynet_coco(coco)
    assert pages[0]["section_order"] == ["Title"]
    assert _CATEGORY_NAMES[11] == "Title"


def test_coco_multi_page_and_empty_page():
    coco = {
        "images": [
            {"id": 1, "width": 1000, "height": 1000},
            {"id": 2, "width": 1000, "height": 1000},  # 어노테이션 없음
        ],
        "annotations": [{"image_id": 1, "category_id": 9, "bbox": [0, 0, 500, 500]}],
        "categories": [{"id": 9, "name": "Table"}],
    }
    pages = parse_doclaynet_coco(coco)
    assert len(pages) == 2
    assert pages[0]["table_structure"]["tables"] == 1
    assert pages[1]["layout_type"] == "unknown"  # 빈 페이지


def test_feature_passes_governance_low_risk():
    # DocLayNet 구조 feature는 원본 미포함 → 거버넌스 통과(approved, low)
    regions = [
        {"category": "Title", "bbox": [0, 0, 1000, 100]},
        {"category": "Text", "bbox": [0, 200, 1000, 600]},
    ]
    feat = parse_doclaynet_page(1000, 1000, regions)
    d = run_pattern_governance(feat)
    assert d.operational is True
    assert d.original_reuse_risk == "low"
    assert d.pattern_status == "approved"
    # 저장 feature엔 원문 없음
    assert "original_text" not in d.abstracted_feature