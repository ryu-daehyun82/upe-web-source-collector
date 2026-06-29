"""License 상태기계 + 자동 1차 분류 (설계서 §4 / v2.1 P-4).

전이 허용표는 계약(Claude 골격). auto_classify 는 휴리스틱 1차 분류.
"""
from __future__ import annotations

import re

from app.models.enums import LicenseStatus

# 허용 전이표 (계약). unknown 에서만 자동분류/수동검토로 분기.
ALLOWED_TRANSITIONS: dict[LicenseStatus, set[LicenseStatus]] = {
    LicenseStatus.unknown: {
        LicenseStatus.allowed,
        LicenseStatus.conditional,
        LicenseStatus.blocked,
    },
    LicenseStatus.conditional: {
        LicenseStatus.conditional_approved,
        LicenseStatus.blocked,
    },
    LicenseStatus.allowed: {LicenseStatus.blocked},
    LicenseStatus.conditional_approved: {LicenseStatus.blocked},
    LicenseStatus.blocked: set(),
}


def can_transition(src: LicenseStatus, dst: LicenseStatus) -> bool:
    """전이 허용 여부(계약). 위반 시 호출부에서 거부."""
    return dst in ALLOWED_TRANSITIONS.get(src, set())


# --- 휴리스틱 사전 ---

# 조건부(NC/ND/SA 등) — 명확히 제약이 붙은 라이선스
_CONDITIONAL_CC_TOKENS = ("by-nc", "by-nd", "by-sa", "nc-", "-nd", "-sa")

# SPDX 식별자 — 자유로운 재사용 가능(허용)
_ALLOWED_SPDX = {
    "cc0-1.0",
    "cc-by-4.0",
    "cc-by-3.0",
    "mit",
    "apache-2.0",
    "bsd-2-clause",
    "bsd-3-clause",
    "isc",
    "unlicense",
    "publicdomain",
}
# SPDX 조건부(copyleft 강·NC/ND 류)
_CONDITIONAL_SPDX = {
    "cc-by-sa-4.0",
    "cc-by-nc-4.0",
    "cc-by-nd-4.0",
    "cc-by-nc-sa-4.0",
    "gpl-3.0",
    "gpl-2.0",
    "agpl-3.0",
    "lgpl-3.0",
}

_CC_URL_RE = re.compile(r"creativecommons\.org/(?:licenses|publicdomain)/([a-z0-9\-]+)", re.I)
_SPDX_RE = re.compile(r"\b([A-Za-z0-9.\-]+)\b")


def _collect_license_clues(meta: dict) -> list[str]:
    """입력 dict 에서 라이선스 단서 문자열 후보를 모은다(모두 소문자)."""
    clues: list[str] = []

    def _add(v) -> None:
        if isinstance(v, str) and v.strip():
            clues.append(v.strip().lower())
        elif isinstance(v, (list, tuple)):
            for x in v:
                _add(x)

    # 흔한 키들: rel=license href, <meta name=license>, spdx, license, copyright, links
    for key in (
        "license",
        "license_url",
        "license_href",
        "rel_license",
        "spdx",
        "spdx_id",
        "meta_license",
        "copyright",
        "rights",
        "dc.rights",
    ):
        if key in meta:
            _add(meta[key])

    # rel="license" 링크 목록(딕셔너리/리스트 형태 모두 수용)
    links = meta.get("links")
    if isinstance(links, (list, tuple)):
        for link in links:
            if isinstance(link, dict):
                rel = str(link.get("rel", "")).lower()
                if "license" in rel:
                    _add(link.get("href"))
                    _add(link.get("title"))

    # raw html 단편이 있으면 전체를 단서로(정규식이 처리)
    for key in ("html", "raw_html", "head_html"):
        if key in meta:
            _add(meta[key])

    return clues


def auto_classify(html_or_meta: dict) -> LicenseStatus:
    """라이선스 자동 1차 분류 (v2.1 P-4, 운영 병목 해소).

    단서: Creative Commons(creativecommons.org), SPDX 식별자,
    <link rel="license">, meta name="license", 저작권 문구.

    반환:
      - 명확한 공개/자유 라이선스 → LicenseStatus.allowed
      - 조건부(NC/ND/SA/copyleft) → LicenseStatus.conditional
      - 단서 없음/모호 → LicenseStatus.unknown (과신 금지 → manual review 라우팅)
    """
    if not isinstance(html_or_meta, dict):
        return LicenseStatus.unknown

    clues = _collect_license_clues(html_or_meta)
    if not clues:
        return LicenseStatus.unknown

    blob = "\n".join(clues)

    # 1) Creative Commons URL 패턴 — variant 코드로 조건부/허용 구분
    found_cc = False
    for m in _CC_URL_RE.finditer(blob):
        found_cc = True
        variant = m.group(1).lower()
        if variant in ("zero", "publicdomain", "mark", "by", "by/4.0", "by/3.0"):
            return LicenseStatus.allowed
        if any(tok in variant for tok in _CONDITIONAL_CC_TOKENS):
            return LicenseStatus.conditional
        # CC variant 인데 정확히 못 가르면 보수적으로 conditional
        return LicenseStatus.conditional

    # 2) SPDX 식별자 토큰 매칭
    tokens = {t.lower() for t in _SPDX_RE.findall(blob)}
    if tokens & _CONDITIONAL_SPDX:
        return LicenseStatus.conditional
    if tokens & _ALLOWED_SPDX:
        return LicenseStatus.allowed

    # 3) 단순 문구 휴리스틱
    if "public domain" in blob or "퍼블릭 도메인" in blob:
        return LicenseStatus.allowed
    if "all rights reserved" in blob or "무단" in blob or "저작권" in blob:
        # 명시적 전적 보유 → 자유 재사용 불가하나, 조건부 검토 대상
        return LicenseStatus.conditional

    # cc 가 나왔지만 위에서 못 갈린 경우(드묾) — conditional
    if found_cc:
        return LicenseStatus.conditional

    # 단서는 있었으나 분류 불가 → 과신 금지
    return LicenseStatus.unknown
