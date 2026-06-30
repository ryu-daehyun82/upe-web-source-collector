"""PPTX 파서 폴백 (§8.4). ZIP 컨테이너에서 슬라이드/미디어 구조 추출.

PDF 폴백과 동일 컨셉: 원문 추출 불가 → original_text="", pattern_text="".
실 텍스트/레이아웃 추출은 python-pptx 어댑터 자리. stdlib(zipfile)만.
"""
from __future__ import annotations

import io
import re
import zipfile

_SLIDE_RE = re.compile(r"ppt/slides/slide\d+\.xml")


def parse_pptx(content: bytes | None, *, url: str | None = None) -> dict:
    """PPTX(ZIP) → raw_feature. 폴백: zip 엔트리에서 슬라이드 수/미디어 추출.

    반환: layout_type("presentation"|"unknown") / section_order(["slide"]*min(n,200)) /
    slide_count / has_images / original_text="" / pattern_text="". url→source_url.
    """
    result = {
        "section_order": [],
        "layout_type": "unknown",
        "slide_count": 0,
        "has_images": False,
        "original_text": "",
        "pattern_text": "",
    }
    if url:
        result["source_url"] = url

    if not content or len(content) < 4 or content[:4] != b"PK\x03\x04":
        return result

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = zf.namelist()
    except (zipfile.BadZipFile, ValueError, EOFError):
        return result

    slide_count = sum(1 for name in names if _SLIDE_RE.match(name))
    result["slide_count"] = slide_count
    result["has_images"] = any(name.startswith("ppt/media/") for name in names)

    if slide_count > 0:
        result["layout_type"] = "presentation"
        result["section_order"] = ["slide"] * min(slide_count, 200)

    return result