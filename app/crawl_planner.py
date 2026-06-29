"""수집측(pre-fetch) 파이프라인 (§13.1 / §13.2 / §2 통합).

frontier 에서 URL 을 꺼내 robots 게이트 → politeness(토큰버킷) → 멱등 잡 enqueue 로
묶는다. robots/politeness/enqueue 는 전부 주입형 — 테스트(가짜)·운영(실 robots/Redis/DB)
분리. async(enqueue 가 DB async 이므로).

robots 통과 후 post-fetch 거버넌스(app/pipeline.py)로 이어진다.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.frontier import Frontier, FrontierItem
from app.policy.robots_checker import check_robots, RobotsDecision


@dataclass
class PlanOutcome:
    """수집 계획 결과.

    decision: blocked_ssrf | blocked_robots | robots_unknown | deferred_politeness
              | enqueued | duplicate_job | planned
    """
    url: str
    domain: str
    depth: int
    decision: str
    job_id: str | None = None
    note: str | None = None


class CrawlPlanner:
    """frontier → robots → politeness → 멱등 enqueue 오케스트레이터.

    robots_check: (domain, path, user_agent) → RobotsDecision (기본 check_robots).
    politeness: .acquire(domain, *, crawl_delay_ms, max_pages_per_day) → bool (None=생략).
    enqueue: async (item) → (job_id, created) (None=영속화 생략, 계획만).
    """

    def __init__(
        self,
        frontier: Frontier,
        *,
        robots_check: Callable[[str, str, str], RobotsDecision] = check_robots,
        politeness=None,
        enqueue: Callable[[FrontierItem], Awaitable[tuple]] | None = None,
        user_agent: str = "UPE-Collector",
        default_crawl_delay_ms: int = 1000,
        max_pages_per_day: int | None = None,
    ) -> None:
        self.frontier = frontier
        self.robots_check = robots_check
        self.politeness = politeness
        self.enqueue = enqueue
        self.user_agent = user_agent
        self.default_crawl_delay_ms = default_crawl_delay_ms
        self.max_pages_per_day = max_pages_per_day

    def _outcome(
        self,
        item: FrontierItem,
        decision: str,
        job_id: str | None = None,
        note: str | None = None,
    ) -> PlanOutcome:
        """FrontierItem 에서 PlanOutcome 조립."""
        return PlanOutcome(
            url=item.url,
            domain=item.domain,
            depth=item.depth,
            decision=decision,
            job_id=job_id,
            note=note,
        )

    async def plan_next(self) -> PlanOutcome | None:
        """frontier 다음 항목 1건 처리. frontier 비면 None."""
        item = self.frontier.next()
        if item is None:
            return None

        path = urlsplit(item.url).path or "/"
        rd = self.robots_check(item.domain, path, self.user_agent)

        # robots 판정(robots_checker 계약).
        if rd.robots_allowed is False and rd.checked is False:
            return self._outcome(item, "blocked_ssrf", note=rd.note)
        if rd.robots_allowed is False and rd.checked is True:
            return self._outcome(item, "blocked_robots", note=rd.note)
        if rd.robots_allowed is None:
            return self._outcome(item, "robots_unknown", note=rd.note)

        # politeness(전역 토큰버킷). robots delay 와 정책 max 중 보수적 큰 값.
        if self.politeness is not None:
            delay = max(self.default_crawl_delay_ms, rd.crawl_delay_ms or 0)
            acquired = self.politeness.acquire(
                item.domain,
                crawl_delay_ms=delay,
                max_pages_per_day=self.max_pages_per_day,
            )
            if not acquired:
                return self._outcome(item, "deferred_politeness")

        # 멱등 enqueue.
        if self.enqueue is not None:
            job_id, created = await self.enqueue(item)
            decision = "enqueued" if created else "duplicate_job"
            return self._outcome(item, decision, job_id=job_id)

        return self._outcome(item, "planned")

    async def plan_all(self, *, limit: int | None = None) -> list[PlanOutcome]:
        """frontier 가 빌 때까지(또는 limit 까지) plan_next 반복."""
        outcomes: list[PlanOutcome] = []
        count = 0
        while True:
            if limit is not None and count >= limit:
                break
            outcome = await self.plan_next()
            if outcome is None:
                break
            outcomes.append(outcome)
            count += 1
        return outcomes