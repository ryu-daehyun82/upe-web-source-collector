"""이미지 파서 폴백 (§8.4). 매직바이트 포맷 판별 + PNG 치수.

PDF 폴백과 동일 컨셉: 원문 추출 불가 → original_text="", pattern_text="".
실 구도/색 추출은 Pillow/gcr-eare 어댑터 자리. stdlib(struct)만.
"""
from __future__ import annotations

import struct


def parse_image(content: bytes | None, *, url: str | None = None) -> dict:
    """이미지 바이트 → raw_feature. 폴백: 매직바이트 포맷 판별 + PNG 치수.

    반환: layout_type("image"|"unknown") / image_format(str|None) / width / height /
    original_text="" / pattern_text="". url→source_url. section_order=[].
    """
    result = {
        "section_order": [],
        "layout_type": "unknown",
        "image_format": None,
        "width": None,
        "height": None,
        "original_text": "",
        "pattern_text": "",
    }
    if url:
        result["source_url"] = url

    if not content or len(content) < 4:
        return result

    if content[:8] == b"\x89PNG\r\n\x1a\n":
        result["image_format"] = "png"
        result["layout_type"] = "image"
        if len(content) >= 24:
            width, height = struct.unpack(">II", content[16:24])
            result["width"] = width
            result["height"] = height
        return result

    if content[:3] == b"\xff\xd8\xff":
        result["image_format"] = "jpeg"
        result["layout_type"] = "image"
        return result

    if content[:6] in (b"GIF87a", b"GIF89a"):
        result["image_format"] = "gif"
        result["layout_type"] = "image"
        return result

    if content[:2] == b"BM":
        result["image_format"] = "bmp"
        result["layout_type"] = "image"
        return result

    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        result["image_format"] = "webp"
        result["layout_type"] = "image"
        return result

    return result