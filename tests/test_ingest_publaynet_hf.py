import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from ingest_publaynet_hf import row_to_regions  # noqa: E402
from app.workers.doclaynet_parser import parse_doclaynet_page  # noqa: E402


def test_xyxy_converted_to_xywh():
    regions = row_to_regions([[56, 469, 271, 554]], ["Text"])
    assert regions == [{"category": "Text", "bbox": [56, 469, 271 - 56, 554 - 469]}]


def test_multiple_and_labels():
    bboxes = [[0, 0, 100, 50], [10, 60, 110, 160]]
    labels = ["Title", "Table"]
    regions = row_to_regions(bboxes, labels)
    assert [r["category"] for r in regions] == ["Title", "Table"]
    assert regions[0]["bbox"] == [0, 0, 100, 50]      # 100>0 & 50>0 → xyxy 변환
    assert regions[1]["bbox"] == [10, 60, 100, 100]


def test_short_or_none_bbox_skipped():
    regions = row_to_regions([[0, 0, 100], None, [0, 0, 10, 10]], ["A", "B", "C"])
    assert len(regions) == 1
    assert regions[0]["category"] == "C"


def test_already_xywh_fallback():
    # x2<=x1 이면 이미 xywh 로 간주(변환 안 함)
    regions = row_to_regions([[5, 5, 0, 0]], ["X"])
    assert regions[0]["bbox"] == [5, 5, 0, 0]


def test_feeds_doclaynet_parser_publaynet_labels():
    # PubLayNet 카테고리(Figure/List 등)도 parser가 이름 기반으로 처리
    bboxes = [[0, 0, 1000, 100], [0, 200, 1000, 700]]
    labels = ["Title", "Figure"]
    regions = row_to_regions(bboxes, labels)
    feat = parse_doclaynet_page(1000, 1000, regions)
    assert feat["layout_type"] == "document"
    assert feat["section_order"] == ["Title", "Figure"]
    assert "Figure" in feat["region_ratios"]