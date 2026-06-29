"""Crawl Frontier 코어 (§13.1 / v2.1 P-2) — 순수 in-memory + 주입형 큐.

흐름: Seed → URL canonicalize → Frontier 큐 → (depth/same-domain/sitemap 제한 +
visited dedup) → (정책 게이트는 외부). Redis 백엔드는 동일 push/pop/__len__ 인터페이스로
나중에 교체(주입). url_canon 재사용, 그 외 stdlib 만.
"""
from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

from app.policy.url_canon import canonicalize_url, extract_domain


@dataclass(frozen=True)
class FrontierItem:
    """Frontier 큐 항목(불변)."""
    url: str       # canonical URL
    domain: str
    depth: int


@dataclass
class FrontierConfig:
    """Frontier 설정. 최대 깊이/동일 도메인 여부/허용 도메인/최대 페이지 수."""
    max_depth: int = 2
    same_domain_only: bool = True
    allowed_domains: frozenset[str] | None = None
    max_pages: int | None = None


class InMemoryFrontierQueue:
    """메모리 기반 FIFO(BFS) 큐. Redis 대비 주입형."""

    def __init__(self) -> None:
        self._queue: deque[FrontierItem] = deque()

    def push(self, item: FrontierItem) -> None:
        """큐에 항목 추가."""
        self._queue.append(item)

    def pop(self) -> FrontierItem | None:
        """큐에서 항목 꺼내기. 비었으면 None."""
        return self._queue.popleft() if self._queue else None

    def __len__(self) -> int:
        """큐에 남은 항목 수."""
        return len(self._queue)


class Frontier:
    """크롤 프론티어. 시드 추가, 링크 처리, 항목 제공."""

    def __init__(
        self,
        config: FrontierConfig | None = None,
        *,
        queue=None,
        seeds: Iterable[str] = (),
    ) -> None:
        """config 기본 FrontierConfig(). queue 기본 InMemoryFrontierQueue()(덕타이핑).

        allowed_domains: config 에 있으면 그대로, 없고 same_domain_only 면 첫 시드 도메인에서 유도.
        """
        self.config = config or FrontierConfig()
        self._queue = queue if queue is not None else InMemoryFrontierQueue()
        self._visited: set[str] = set()
        self._accepted_count: int = 0

        if self.config.allowed_domains is not None:
            self._allowed_domains: set[str] = set(self.config.allowed_domains)
        else:
            self._allowed_domains = set()

        for seed in seeds:
            self.add_seed(seed)

    def _domain_allowed(self, domain: str) -> bool:
        """도메인 허용 여부. same_domain_only False면 항상 True;
        allowed 비었으면(미정) True(첫 시드 허용); 아니면 domain in allowed."""
        if not self.config.same_domain_only:
            return True
        if not self._allowed_domains:
            return True
        return domain in self._allowed_domains

    def _enqueue(self, raw_url: str, depth: int) -> FrontierItem | None:
        """canonicalize → depth/domain/visited/max_pages 게이트 → push. 거부 시 None."""
        try:
            canonical = canonicalize_url(raw_url)
        except ValueError:
            return None

        if depth > self.config.max_depth:
            return None

        domain = extract_domain(canonical)
        if not self._domain_allowed(domain):
            return None

        if canonical in self._visited:
            return None

        if self.config.max_pages is not None and self._accepted_count >= self.config.max_pages:
            return None

        self._visited.add(canonical)
        self._accepted_count += 1
        item = FrontierItem(url=canonical, domain=domain, depth=depth)
        self._queue.push(item)
        return item

    def add_seed(self, url: str) -> FrontierItem | None:
        """시드 URL 추가(depth=0). same_domain_only이고 allowed 미정이면 이 도메인을 allowed로 정의."""
        try:
            canonical = canonicalize_url(url)
        except ValueError:
            return None

        domain = extract_domain(canonical)
        if self.config.same_domain_only and not self._allowed_domains:
            self._allowed_domains.add(domain)

        return self._enqueue(canonical, 0)

    def add_links(self, parent: FrontierItem, links: Iterable[str]) -> list[FrontierItem]:
        """부모 기준 urljoin 절대화 후 depth+1 로 enqueue. accept 된 항목만 반환."""
        accepted: list[FrontierItem] = []
        for link in links:
            absolute_url = urljoin(parent.url, link)
            item = self._enqueue(absolute_url, parent.depth + 1)
            if item is not None:
                accepted.append(item)
        return accepted

    def next(self) -> FrontierItem | None:
        """큐에서 다음 항목 꺼내기."""
        return self._queue.pop()

    def __len__(self) -> int:
        """큐에 대기 중인 항목 수."""
        return len(self._queue)

    @property
    def visited_count(self) -> int:
        """방문(적재)한 URL 수."""
        return len(self._visited)

    def stats(self) -> dict:
        """프론티어 상태 요약."""
        return {
            "pending": len(self._queue),
            "visited": len(self._visited),
            "accepted": self._accepted_count,
            "allowed_domains": sorted(self._allowed_domains),
        }


class _LinkParser(HTMLParser):
    """HTML <a href> 추출 파서."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """<a> 태그의 href 속성 수집."""
        if tag == "a":
            for attr_name, attr_value in attrs:
                if attr_name == "href" and attr_value is not None:
                    self.links.append(attr_value)


def extract_links(html: str, base_url: str) -> list[str]:
    """HTML <a href> 추출 → 절대 URL. mailto/javascript/tel/순수 fragment 제외, 중복 제거(순서 보존)."""
    if not html:
        return []

    parser = _LinkParser()
    parser.feed(html)
    seen: set[str] = set()
    result: list[str] = []

    for href in parser.links:
        if href.startswith(("mailto:", "javascript:", "tel:", "#")):
            continue
        absolute = urljoin(base_url, href)
        if absolute not in seen:
            seen.add(absolute)
            result.append(absolute)
    return result


_SITEMAP_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)


def parse_sitemap(xml: str) -> list[str]:
    """사이트맵 XML 의 <loc> URL 추출(네임스페이스 무관). 빈/None 이면 []. 순서 보존."""
    if not xml:
        return []
    urls: list[str] = []
    for match in _SITEMAP_LOC_RE.finditer(xml):
        url = match.group(1).strip()
        if url:
            urls.append(url)
    return urls