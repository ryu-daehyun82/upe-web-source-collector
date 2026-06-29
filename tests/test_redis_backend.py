import pytest

fakeredis = pytest.importorskip("fakeredis")

from app.frontier import FrontierItem
from app.redis_backend import RedisFrontierQueue, RedisPolitenessLimiter


@pytest.fixture()
def r():
    return fakeredis.FakeStrictRedis()


class TestRedisFrontierQueue:
    def test_queue_fifo_and_len(self, r):
        q = RedisFrontierQueue(r)
        assert len(q) == 0
        assert q.pop() is None

        item1 = FrontierItem("https://ex.com/1", "ex.com", 1)
        item2 = FrontierItem("https://ex.com/2", "ex.com", 2)
        q.push(item1)
        q.push(item2)
        assert len(q) == 2

        popped1 = q.pop()
        assert popped1.url == "https://ex.com/1"
        assert popped1.domain == "ex.com"
        assert popped1.depth == 1

        popped2 = q.pop()
        assert popped2.url == "https://ex.com/2"
        assert popped2.domain == "ex.com"
        assert popped2.depth == 2

        assert len(q) == 0
        assert q.pop() is None

    def test_queue_preserves_fields(self, r):
        q = RedisFrontierQueue(r)
        item = FrontierItem("https://ex.com/a", "ex.com", 3)
        q.push(item)
        popped = q.pop()
        assert popped.url == "https://ex.com/a"
        assert popped.domain == "ex.com"
        assert popped.depth == 3

    def test_two_queues_isolated_by_key(self, r):
        q1 = RedisFrontierQueue(r, key="k1")
        q2 = RedisFrontierQueue(r, key="k2")
        item = FrontierItem("https://ex.com", "ex.com", 0)
        q1.push(item)
        assert len(q1) == 1
        assert len(q2) == 0


class TestRedisPolitenessLimiter:
    def test_delay_blocks_second(self, r):
        lim = RedisPolitenessLimiter(r)
        assert lim.acquire("ex.com", crawl_delay_ms=10000) is True
        assert lim.acquire("ex.com", crawl_delay_ms=10000) is False

    def test_delay_independent_domains(self, r):
        lim = RedisPolitenessLimiter(r)
        assert lim.acquire("a.com", crawl_delay_ms=10000) is True
        assert lim.acquire("b.com", crawl_delay_ms=10000) is True

    def test_daily_cap(self, r):
        lim = RedisPolitenessLimiter(r)
        assert lim.acquire("ex.com", max_pages_per_day=2) is True
        assert lim.acquire("ex.com", max_pages_per_day=2) is True
        assert lim.acquire("ex.com", max_pages_per_day=2) is False
        assert lim.remaining_today("ex.com", 2) == 0

    def test_daily_cap_remaining(self, r):
        lim = RedisPolitenessLimiter(r)
        assert lim.remaining_today("fresh.com", 5) == 5
        assert lim.acquire("fresh.com", max_pages_per_day=5) is True
        assert lim.remaining_today("fresh.com", 5) == 4

    def test_reset_reallows(self, r):
        lim = RedisPolitenessLimiter(r)
        assert lim.acquire("ex.com", crawl_delay_ms=10000) is True
        assert lim.acquire("ex.com", crawl_delay_ms=10000) is False
        lim.reset("ex.com")
        assert lim.acquire("ex.com", crawl_delay_ms=10000) is True

    def test_both_constraints_pass(self, r):
        lim = RedisPolitenessLimiter(r)
        assert lim.acquire("ex.com", crawl_delay_ms=0, max_pages_per_day=1) is True
        assert lim.acquire("ex.com", crawl_delay_ms=0, max_pages_per_day=1) is False