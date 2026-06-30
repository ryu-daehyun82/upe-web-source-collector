"""비디오 파서 폴백 (§8.4). 컨테이너 매직 판별(메타 한정).

PDF 폴백과 동일 컨셉: 원문 추출 불가 → original_text="", pattern_text="".
실 shot/scene/subtitle 추출은 ffprobe/ffmpeg 어댑터 자리. stdlib만.
"""
from __future__ import annotations


def parse_video(content: bytes | None, *, url: str | None = None) -> dict:
    """비디오 바이트 → raw_feature. 폴백: 컨테이너 매직 판별.

    반환: layout_type("video"|"unknown") / video_container(str|None) /
    original_text="" / pattern_text="". url→source_url. section_order=[].
    """
    result = {
        "section_order": [],
        "layout_type": "unknown",
        "video_container": None,
        "original_text": "",
        "pattern_text": "",
    }
    if url:
        result["source_url"] = url

    if not content or len(content) < 12:
        return result

    if content[4:8] == b"ftyp":
        result["video_container"] = "mp4"
        result["layout_type"] = "video"
        return result

    if content[:4] == b"\x1a\x45\xdf\xa3":
        result["video_container"] = "matroska"
        result["layout_type"] = "video"
        return result

    if content[:4] == b"RIFF" and content[8:12] == b"AVI ":
        result["video_container"] = "avi"
        result["layout_type"] = "video"
        return result

    return result