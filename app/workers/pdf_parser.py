"""PDF 파서 어댑터+폴백 (§8.4 Parser Worker 폴백).

PDF bytes → 거버넌스(run_pattern_governance) 입력 raw_feature. 실 텍스트/레이아웃
추출은 pdf-layer-rebuilder 어댑터 자리 — 폴백은 stdlib 바이트 정규식으로 뽑을 수 있는
거친 구조 신호만(원문 추출 불가 → original_text="" → 누출 없음). 순수 stdlib.
"""
from __future__ import annotations

import importlib
import re

# PDF 페이지/리소스 시그니처(바이트 정규식)
_PAGE_RE = re.compile(rb"/Type\s*/Page\b")     # 개별 페이지(/Pages 트리는 \b 로 제외)
_IMAGE_RE = re.compile(rb"/Subtype\s*/Image")
_FONT_RE = re.compile(rb"/Font\b")
_ENCRYPT_RE = re.compile(rb"/Encrypt\b")


def _is_pdf(content: bytes) -> bool:
    """content 가 b"%PDF" 로 시작하면 True."""
    return content.startswith(b"%PDF")


# 외부 실 파서 연결지점(현재 미존재 → ImportError 폴백):
_PDF_ADAPTER_MODULE = "pdf_layer_rebuilder.extractor"


def _try_load_adapter():
    """실 PDF 어댑터 로드 시도. 실패(ImportError) 시 None."""
    try:
        return importlib.import_module(_PDF_ADAPTER_MODULE)
    except ImportError:
        return None


def parse_pdf(content: bytes | None, *, url: str | None = None) -> dict:
    """PDF bytes → raw_feature(거버넌스 입력).

    폴백: 바이트 정규식으로 page_count/has_images/has_text/encrypted 추출.
    원문/레이아웃 추출 불가 → original_text="", pattern_text=""(누출 없음 정상).

    반환 키: layout_type(document|encrypted|unknown) / section_order(["page"]*min(n,200)) /
    page_count / has_images / has_text / encrypted / original_text="" / pattern_text="".
    url 주어지면 source_url 포함.
    """
    result = {
        "layout_type": "unknown",
        "section_order": [],
        "page_count": 0,
        "has_images": False,
        "has_text": False,
        "encrypted": False,
        "original_text": "",
        "pattern_text": "",
    }
    if url is not None:
        result["source_url"] = url

    # None/빈/비PDF
    if not content or not _is_pdf(content):
        return result

    # 암호화
    if _ENCRYPT_RE.search(content):
        result["encrypted"] = True
        result["layout_type"] = "encrypted"
        return result

    page_count = len(_PAGE_RE.findall(content))
    result["page_count"] = page_count
    if page_count > 0:
        result["layout_type"] = "document"
        result["section_order"] = ["page"] * min(page_count, 200)

    if _IMAGE_RE.search(content):
        result["has_images"] = True
    if _FONT_RE.search(content):
        result["has_text"] = True

    # 실 어댑터(_try_load_adapter)가 있으면 위임이 원칙 — 본 파일럿은 매핑 불명확 → 폴백.
    return result