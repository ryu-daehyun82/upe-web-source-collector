"""fallback HTML 파서 (§7.1 / §8.4 폴백).

fetch 한 HTML 텍스트 → 거버넌스(run_pattern_governance)가 먹는 raw_feature dict.
실 layout/color 추출은 gcr-eare 어댑터 자리 — 폴백은 **구조 메타 + 원문(누출검사용)**만.
abstraction_guard 화이트리스트(section_order/table_structure/card_count/layout_type) 위주 출력.
순수 stdlib(html.parser).
"""
from __future__ import annotations

from html.parser import HTMLParser

# 구조 태그(섹션 순서 집계 대상)
_STRUCTURE_TAGS = ("header", "nav", "main", "section", "article", "aside", "footer")
# 카드 유사 블록
_CARD_TAGS = ("article", "li")
# 텍스트 제외(스크립트/스타일)
_SKIP_TEXT_TAGS = ("script", "style")


class _HtmlStructureParser(HTMLParser):
    """HTML 구조 파서 — 섹션 순서·테이블·카드·헤딩·텍스트 수집."""

    def __init__(self) -> None:
        super().__init__()
        self.section_order: list[str] = []
        self.table_count = 0
        self.tr_count = 0
        self.card_count = 0
        self.heading_count = 0
        self._skip_depth = 0
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _STRUCTURE_TAGS:
            self.section_order.append(tag)
        if tag == "table":
            self.table_count += 1
        if tag == "tr":
            self.tr_count += 1
        if tag in _CARD_TAGS:
            self.card_count += 1
        if len(tag) == 2 and tag[0] == "h" and tag[1] in "123456":
            self.heading_count += 1
        if tag in _SKIP_TEXT_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TEXT_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._text_parts.append(data.strip())


def _layout_type(section_order: list[str]) -> str:
    """섹션 순서로 레이아웃 유형 판단."""
    if "article" in section_order:
        return "article"
    if len(set(section_order)) >= 3:
        return "multi_section"
    if not section_order:
        return "unknown"
    return "page"


def parse_html(html: str | None, *, url: str | None = None) -> dict:
    """HTML → raw_feature(거버넌스 입력). 빈/None 이면 최소 구조(빈 텍스트).

    반환 키: section_order / table_structure{tables,rows} / card_count / layout_type /
    original_text(추출 본문, guard 가 _leak_probe 로 분리) / pattern_text=""(원문 미보존).
    url 주어지면 source_url 메타 포함.
    """
    if not html:
        result = {
            "section_order": [],
            "table_structure": {"tables": 0, "rows": 0},
            "card_count": 0,
            "layout_type": "unknown",
            "original_text": "",
            "pattern_text": "",
        }
        if url is not None:
            result["source_url"] = url
        return result

    parser = _HtmlStructureParser()
    parser.feed(html)

    result = {
        "section_order": parser.section_order,
        "table_structure": {"tables": parser.table_count, "rows": parser.tr_count},
        "card_count": parser.card_count,
        "layout_type": _layout_type(parser.section_order),
        "original_text": " ".join(parser._text_parts),
        "pattern_text": "",
    }
    if url is not None:
        result["source_url"] = url
    return result