import asyncio

from app.crawl_planner import CrawlPlanner
from app.frontier import Frontier, FrontierConfig
from app.policy.robots_checker import RobotsDecision


def _robots(allowed, checked=True, delay_ms=None, note=""):
    def _check(domain, path, ua):
        return RobotsDecision(domain=domain, robots_allowed=allowed, crawl_delay_ms=delay_ms,
                              sitemaps=[], checked=checked, note=note)
    return _check


class _FakePoliteness:
    def __init__(self, allow=True):
        self.allow = allow
        self.calls = []

    def acquire(self, domain, *, crawl_delay_ms, max_pages_per_day=None):
        self.calls.append((domain, crawl_delay_ms, max_pages_per_day))
        return self.allow


def _make_enqueue(created=True, job_id="job-1", recorder=None):
    async def _enqueue(item):
        if recorder is not None:
            recorder.append(item)
        return (job_id, created)
    return _enqueue


def _frontier(urls, **cfg):
    return Frontier(config=FrontierConfig(**cfg), seeds=list(urls))


def test_planned_when_allowed_no_enqueue():
    f = _frontier(["https://ex.com/a"])
    p = CrawlPlanner(f, robots_check=_robots(True))
    out = asyncio.run(p.plan_next())
    assert out.decision == "planned"
    assert "ex.com" in out.url
    assert out.job_id is None


def test_blocked_robots():
    f = _frontier(["https://ex.com/a"])
    rec = []
    enq = _make_enqueue(recorder=rec)
    p = CrawlPlanner(f, robots_check=_robots(False, checked=True), enqueue=enq)
    out = asyncio.run(p.plan_next())
    assert out.decision == "blocked_robots"
    assert rec == []


def test_blocked_ssrf():
    f = _frontier(["https://ex.com/a"])
    robots_check = _robots(False, checked=False, note="SSRF blocked: private ip")
    p = CrawlPlanner(f, robots_check=robots_check)
    out = asyncio.run(p.plan_next())
    assert out.decision == "blocked_ssrf"
    assert "SSRF" in out.note


def test_robots_unknown():
    f = _frontier(["https://ex.com/a"])
    robots_check = _robots(None, checked=False)
    p = CrawlPlanner(f, robots_check=robots_check)
    out = asyncio.run(p.plan_next())
    assert out.decision == "robots_unknown"


def test_enqueued():
    f = _frontier(["https://ex.com/a"])
    enq = _make_enqueue(created=True, job_id="J1")
    p = CrawlPlanner(f, robots_check=_robots(True), enqueue=enq)
    out = asyncio.run(p.plan_next())
    assert out.decision == "enqueued"
    assert out.job_id == "J1"


def test_duplicate_job():
    f = _frontier(["https://ex.com/a"])
    enq = _make_enqueue(created=False, job_id="J2")
    p = CrawlPlanner(f, robots_check=_robots(True), enqueue=enq)
    out = asyncio.run(p.plan_next())
    assert out.decision == "duplicate_job"
    assert out.job_id == "J2"


def test_deferred_politeness():
    f = _frontier(["https://ex.com/a"])
    pol = _FakePoliteness(allow=False)
    rec = []
    enq = _make_enqueue(recorder=rec)
    p = CrawlPlanner(f, robots_check=_robots(True), politeness=pol, enqueue=enq)
    out = asyncio.run(p.plan_next())
    assert out.decision == "deferred_politeness"
    assert rec == []


def test_politeness_delay_conservative():
    pol = _FakePoliteness(allow=True)
    p = CrawlPlanner(_frontier(["https://ex.com/a"]), robots_check=_robots(True, delay_ms=5000),
                     politeness=pol, default_crawl_delay_ms=1000)
    asyncio.run(p.plan_next())
    assert pol.calls[0][1] == 5000


def test_politeness_delay_default_when_robots_smaller():
    pol = _FakePoliteness(allow=True)
    p = CrawlPlanner(_frontier(["https://ex.com/a"]), robots_check=_robots(True, delay_ms=None),
                     politeness=pol, default_crawl_delay_ms=1000)
    asyncio.run(p.plan_next())
    assert pol.calls[0][1] == 1000


def test_plan_all_processes_frontier():
    f = _frontier(["https://ex.com/a", "https://ex.com/b", "https://ex.com/c"])
    p = CrawlPlanner(f, robots_check=_robots(True))
    outs = asyncio.run(p.plan_all())
    assert len(outs) == 3
    assert all(o.decision == "planned" for o in outs)


def test_plan_all_limit():
    f = _frontier(["https://ex.com/a", "https://ex.com/b", "https://ex.com/c"])
    p = CrawlPlanner(f, robots_check=_robots(True))
    outs = asyncio.run(p.plan_all(limit=2))
    assert len(outs) == 2


def test_plan_next_empty_frontier():
    f = _frontier([])
    p = CrawlPlanner(f, robots_check=_robots(True))
    assert asyncio.run(p.plan_next()) is None
    assert asyncio.run(p.plan_all()) == []