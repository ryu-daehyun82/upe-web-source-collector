"""Redis 백엔드 (§13.1 큐 / §13.2 politeness).

(1) RedisFrontierQueue — InMemoryFrontierQueue 와 동일 push/pop/__len__ 인터페이스의
    Redis 리스트 구현(분산 워커 공유 frontier).
(2) RedisPolitenessLimiter — 도메인별 crawl_delay + 일일 상한을 전 워커가 공유하는
    토큰버킷(로컬이 아니라 Redis 로 전역 강제).

redis 클라이언트는 생성자 주입(덕타이핑) — 운영은 실 redis-py, 테스트는 fakeredis.
모듈에서 redis 를 import 하지 않는다.
"""
from __future__ import annotations

import json

from app.frontier import FrontierItem


class RedisFrontierQueue:
    """Redis 기반 FIFO 큐. InMemoryFrontierQueue 와 동일 인터페이스."""

    def __init__(self, redis, *, key: str = "upe:frontier") -> None:
        self.redis = redis
        self.key = key

    def push(self, item: FrontierItem) -> None:
        """FrontierItem 을 JSON 직렬화해 리스트 뒤(rpush)에 추가."""
        payload = json.dumps({"url": item.url, "domain": item.domain, "depth": item.depth})
        self.redis.rpush(self.key, payload)

    def pop(self) -> FrontierItem | None:
        """리스트 앞(lpop)에서 꺼내 FrontierItem 복원. 비면 None."""
        result = self.redis.lpop(self.key)
        if result is None:
            return None
        if isinstance(result, bytes):
            result = result.decode("utf-8")
        data = json.loads(result)
        return FrontierItem(url=data["url"], domain=data["domain"], depth=data["depth"])

    def __len__(self) -> int:
        """큐 길이(llen)."""
        return self.redis.llen(self.key)


class RedisPolitenessLimiter:
    """Redis 기반 도메인별 politeness(crawl delay + 일일 상한). 전 워커 공유."""

    def __init__(self, redis, *, namespace: str = "upe:politeness") -> None:
        self.redis = redis
        self.ns = namespace

    def acquire(
        self,
        domain: str,
        *,
        crawl_delay_ms: int = 0,
        max_pages_per_day: int | None = None,
    ) -> bool:
        """도메인 크롤 허가. delay 게이트 + 일일 상한 둘 다 통과해야 True."""
        # 1) crawl_delay 게이트(먼저). SET NX PX 로 delay_ms 동안 잠금.
        if crawl_delay_ms > 0:
            dkey = f"{self.ns}:delay:{domain}"
            acquired = self.redis.set(dkey, 1, nx=True, px=crawl_delay_ms)
            if not acquired:
                return False  # 아직 delay 안 지남 — 카운터 미변경

        # 2) 일일 상한. INCR 후 첫 증가에 24h TTL(롤링). 초과면 DECR 롤백.
        if max_pages_per_day is not None:
            ckey = f"{self.ns}:count:{domain}"
            count = self.redis.incr(ckey)
            if count == 1:
                self.redis.expire(ckey, 86400)
            if count > max_pages_per_day:
                self.redis.decr(ckey)
                return False

        return True

    def remaining_today(self, domain: str, max_pages_per_day: int) -> int:
        """오늘 남은 크롤 가능 수 = max(0, 상한 - 현재 count)."""
        ckey = f"{self.ns}:count:{domain}"
        result = self.redis.get(ckey)
        if result is None:
            return max_pages_per_day
        if isinstance(result, bytes):
            result = result.decode("utf-8")
        count = int(result)
        return max(0, max_pages_per_day - count)

    def reset(self, domain: str) -> None:
        """도메인의 delay/count 키 삭제(테스트·운영 리셋)."""
        self.redis.delete(f"{self.ns}:delay:{domain}", f"{self.ns}:count:{domain}")