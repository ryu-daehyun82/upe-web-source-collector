"""robots_checker 단위테스트 — fetch monkeypatch + SSRF (네트워크 없음)."""
import app.policy.robots_checker as rc
from app.policy.robots_checker import check_robots

SAMPLE = """
User-agent: *
Disallow: /private/
Allow: /public/
Crawl-delay: 2
Sitemap: https://example.com/sitemap.xml
Sitemap: https://example.com/news-sitemap.xml
"""


def _patch_fetch(monkeypatch, text, note=""):
    monkeypatch.setattr(rc, "_fetch_robots_text", lambda domain, timeout=5.0: (text, note))


def test_allow_public_path(monkeypatch):
    _patch_fetch(monkeypatch, SAMPLE)
    d = check_robots("example.com", "/public/page")
    assert d.robots_allowed is True
    assert d.checked is True


def test_disallow_private_path(monkeypatch):
    _patch_fetch(monkeypatch, SAMPLE)
    d = check_robots("example.com", "/private/secret")
    assert d.robots_allowed is False
    assert d.checked is True


def test_crawl_delay_parsed_to_ms(monkeypatch):
    _patch_fetch(monkeypatch, SAMPLE)
    d = check_robots("example.com", "/public/x")
    assert d.crawl_delay_ms == 2000


def test_sitemaps_collected(monkeypatch):
    _patch_fetch(monkeypatch, SAMPLE)
    d = check_robots("example.com", "/public/x")
    assert "https://example.com/sitemap.xml" in d.sitemaps
    assert "https://example.com/news-sitemap.xml" in d.sitemaps


def test_fetch_failure_is_unconfirmed(monkeypatch):
    _patch_fetch(monkeypatch, None, note="fetch error: ConnectError")
    d = check_robots("example.com", "/x")
    assert d.robots_allowed is None  # 미확인 (차단 아님)
    assert d.checked is False


def test_empty_robots_allows_all(monkeypatch):
    _patch_fetch(monkeypatch, "", note="404")
    d = check_robots("example.com", "/anything")
    assert d.robots_allowed is True
    assert d.checked is True


def test_ssrf_localhost_blocked():
    d = check_robots("localhost", "/x")
    assert d.robots_allowed is False
    assert d.checked is False
    assert "SSRF" in d.note


def test_ssrf_metadata_ip_blocked():
    d = check_robots("169.254.169.254", "/latest/meta-data/")
    assert d.robots_allowed is False
    assert d.checked is False


def test_ssrf_private_ip_literal_blocked():
    d = check_robots("127.0.0.1", "/x")
    assert d.robots_allowed is False
    assert d.checked is False


def test_ssrf_blocked_does_not_fetch(monkeypatch):
    # SSRF 차단이 fetch 보다 먼저 — fetch 호출되면 예외로 실패 유도
    def _boom(*a, **k):
        raise AssertionError("fetch must not be called for blocked host")

    monkeypatch.setattr(rc, "_fetch_robots_text", _boom)
    d = check_robots("10.0.0.5", "/x")
    assert d.robots_allowed is False
