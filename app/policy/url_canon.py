"""URL 정규화 헬퍼 (설계서 §6.1).

표준 라이브러리만 사용(self-contained). 중복 등록 판정의 기반이 되는
canonical_url 을 만든다.

정규화 규칙:
  1. scheme/host 소문자화. scheme 누락 시 https 보정.
  2. 기본 포트 제거(http:80, https:443).
  3. fragment(#...) 제거.
  4. 쿼리 파라미터 키 기준 정렬(값 보존). 빈 쿼리는 제거.
  5. path trailing slash 정규화: 루트("/")는 유지, 그 외 말미 "/" 제거.
  6. 빈 path 는 "/" 로.
"""
from __future__ import annotations

from urllib.parse import (
    parse_qsl,
    urlencode,
    urlsplit,
    urlunsplit,
)

_DEFAULT_PORTS = {"http": "80", "https": "443"}


def canonicalize_url(raw: str) -> str:
    """URL 을 정규형으로 변환. 입력이 비정상이면 가능한 한 보존하며 정규화."""
    if raw is None:
        raise ValueError("url is None")
    url = raw.strip()
    if not url:
        raise ValueError("url is empty")

    # scheme 누락 보정(예: "example.com/x" → "https://example.com/x")
    if "://" not in url:
        url = "https://" + url

    parts = urlsplit(url)

    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()

    # 포트 정규화 — 기본 포트는 제거
    port = parts.port
    netloc = hostname
    if port is not None and str(port) != _DEFAULT_PORTS.get(scheme):
        netloc = f"{hostname}:{port}"

    # userinfo 보존(드물지만 손실 방지)
    if parts.username:
        userinfo = parts.username
        if parts.password:
            userinfo += f":{parts.password}"
        netloc = f"{userinfo}@{netloc}"

    # path trailing slash 정규화
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    if path == "":
        path = "/"

    # 쿼리 정렬(키 우선, 동일 키는 값 순). 빈 값 파라미터도 보존.
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    query_pairs.sort(key=lambda kv: (kv[0], kv[1]))
    query = urlencode(query_pairs)

    # fragment 제거(빈 문자열)
    return urlunsplit((scheme, netloc, path, query, ""))


def extract_domain(raw: str) -> str:
    """URL 에서 도메인(host, 포트 제외, 소문자) 추출."""
    url = raw.strip()
    if "://" not in url:
        url = "https://" + url
    host = urlsplit(url).hostname or ""
    return host.lower()
