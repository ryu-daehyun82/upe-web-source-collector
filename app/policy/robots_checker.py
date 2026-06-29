"""robots.txt 정책 체커 (설계서 §3, §8 / v2.1 P-3).

표준 라이브러리 `urllib.robotparser` 기반 — 간단하고 견고.
fetch 만 httpx 로 분리(타임아웃/실패 처리·테스트 monkeypatch 용이).

판정 원칙(보수적):
  - robots.txt fetch 실패/미존재 → robots_allowed=None, checked=False (차단 아님, 미확인)
  - SSRF 위험 도메인(private/localhost/metadata) → robots_allowed=False, checked=False (차단)
  - 정상 파싱 → user-agent 규칙으로 path 허용/차단 판정

재사용 지점(주석): robots.txt fetch 는 emotional-support-harvester `crawler` 의
fetch 베이스로 교체 가능. Sprint 0 은 크로스레포 결합 회피 위해 self-contained.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib import robotparser
from urllib.parse import urlsplit


@dataclass
class RobotsDecision:
    domain: str
    robots_allowed: bool | None  # None = robots.txt 미확인/오류
    crawl_delay_ms: int | None
    sitemaps: list[str]
    checked: bool
    note: str = ""


DEFAULT_TIMEOUT_S = 5.0

# SSRF: 차단 호스트명(정확히 일치)
_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
}
# 클라우드 메타데이터 엔드포인트
_METADATA_IPS = {"169.254.169.254", "fd00:ec2::254"}


def _is_private_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or ip_str in _METADATA_IPS
    )


def _is_blocked_host(domain: str) -> tuple[bool, str]:
    """SSRF 방어: 차단 대상이면 (True, 사유)."""
    host = domain.strip().lower()
    if not host:
        return True, "empty host"
    if host in _BLOCKED_HOSTNAMES:
        return True, f"blocked hostname: {host}"
    if host in _METADATA_IPS:
        return True, f"metadata endpoint: {host}"

    # 호스트가 IP 리터럴이면 직접 검사
    if _is_private_ip(host):
        return True, f"private/loopback ip literal: {host}"

    # 호스트명 → IP 해석 후 검사(베스트 에포트; 해석 실패는 차단하지 않음=미확인)
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False, ""
    for info in infos:
        sockaddr = info[4]
        ip = sockaddr[0]
        if _is_private_ip(ip):
            return True, f"resolves to private ip: {ip}"
    return False, ""


def _fetch_robots_text(domain: str, timeout: float = DEFAULT_TIMEOUT_S) -> tuple[str | None, str]:
    """https://{domain}/robots.txt 본문 반환. 실패 시 (None, note).

    테스트에서 monkeypatch 대상.
    """
    import httpx  # 지역 임포트 — 테스트에서 fetch 자체를 monkeypatch 할 때 무관하게.

    url = f"https://{domain}/robots.txt"
    try:
        resp = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "UPE-Collector"},
        )
    except Exception as exc:  # noqa: BLE001 — 네트워크 오류 전반은 미확인 처리
        return None, f"fetch error: {type(exc).__name__}"

    if resp.status_code == 404:
        # robots.txt 부재 → 표준상 전체 허용이나, 명시 robots 없음을 note 로 남김
        return "", "robots.txt 404 (treated as permissive)"
    if resp.status_code >= 400:
        return None, f"http {resp.status_code}"
    return resp.text, ""


def check_robots(domain: str, path: str, user_agent: str = "UPE-Collector") -> RobotsDecision:
    """domain/path 가 robots.txt 상 허용인지 판정.

    1. SSRF 방어(private/localhost/metadata 차단)
    2. robots.txt fetch(실패=미확인)
    3. urllib.robotparser 로 user-agent 규칙·crawl-delay·sitemap 파싱
    """
    # path 정규화 — 절대경로 보장
    if not path:
        path = "/"
    if "://" in path:
        path = urlsplit(path).path or "/"
    if not path.startswith("/"):
        path = "/" + path

    blocked, reason = _is_blocked_host(domain)
    if blocked:
        return RobotsDecision(
            domain=domain,
            robots_allowed=False,
            crawl_delay_ms=None,
            sitemaps=[],
            checked=False,
            note=f"SSRF blocked: {reason}",
        )

    text, note = _fetch_robots_text(domain)
    if text is None:
        # 미확인 — 보수적으로 None(차단 아님, 호출부에서 manual_review 로 라우팅)
        return RobotsDecision(
            domain=domain,
            robots_allowed=None,
            crawl_delay_ms=None,
            sitemaps=[],
            checked=False,
            note=note or "robots unavailable",
        )

    parser = robotparser.RobotFileParser()
    parser.parse(text.splitlines())

    allowed = parser.can_fetch(user_agent, path)

    # crawl-delay (초 → ms). robotparser 는 매칭 user-agent 의 delay 반환.
    crawl_delay_ms: int | None = None
    try:
        delay = parser.crawl_delay(user_agent)
        if delay is not None:
            crawl_delay_ms = int(float(delay) * 1000)
    except Exception:  # noqa: BLE001 — 구버전 호환
        crawl_delay_ms = None

    # sitemaps
    sitemaps: list[str] = []
    try:
        sm = parser.site_maps()
        if sm:
            sitemaps = list(sm)
    except Exception:  # noqa: BLE001
        sitemaps = []

    return RobotsDecision(
        domain=domain,
        robots_allowed=bool(allowed),
        crawl_delay_ms=crawl_delay_ms,
        sitemaps=sitemaps,
        checked=True,
        note=note,
    )
