from app.frontier import (
    Frontier, FrontierConfig, FrontierItem, InMemoryFrontierQueue,
    extract_links, parse_sitemap,
)


def test_seed_enqueue_and_next():
    f = Frontier(config=FrontierConfig(), seeds=["https://ex.com/a"])
    item = f.next()
    assert item is not None
    assert "ex.com" in item.url
    assert item.depth == 0
    assert f.next() is None


def test_duplicate_seed_rejected():
    f = Frontier(config=FrontierConfig())
    first = f.add_seed("https://ex.com/a")
    assert first is not None
    second = f.add_seed("https://ex.com/a")
    assert second is None


def test_max_depth_limit():
    config = FrontierConfig(max_depth=1, same_domain_only=True)
    f = Frontier(config=config, seeds=["https://ex.com/"])
    seed = f.next()
    assert seed is not None
    assert seed.depth == 0
    accepted = f.add_links(seed, ["https://ex.com/child"])
    assert len(accepted) == 1
    assert accepted[0].depth == 1
    child = f.next()
    assert child is not None
    assert child.url == accepted[0].url
    assert f.add_links(child, ["https://ex.com/grand"]) == []


def test_same_domain_only_blocks_external():
    config = FrontierConfig(same_domain_only=True)
    f = Frontier(config=config, seeds=["https://ex.com/"])
    seed = f.next()
    assert seed is not None
    accepted = f.add_links(seed, ["https://other.com/x", "https://ex.com/y"])
    assert len(accepted) == 1
    assert "ex.com" in accepted[0].url
    assert "other.com" not in accepted[0].url


def test_allowed_domains_explicit():
    config = FrontierConfig(allowed_domains=frozenset({"a.com"}), same_domain_only=True)
    f = Frontier(config=config)
    assert f.add_seed("https://b.com/") is None
    assert f.add_seed("https://a.com/") is not None


def test_max_pages_cap():
    config = FrontierConfig(max_pages=2, same_domain_only=False)
    f = Frontier(config=config)
    seeds = ["https://x.com/a", "https://y.com/b", "https://z.com/c"]
    results = [f.add_seed(url) for url in seeds]
    assert results[0] is not None
    assert results[1] is not None
    assert results[2] is None
    stats = f.stats()
    assert stats['accepted'] == 2


def test_same_domain_off_allows_any():
    config = FrontierConfig(same_domain_only=False)
    f = Frontier(config=config, seeds=["https://ex.com/"])
    seed = f.next()
    assert seed is not None
    accepted = f.add_links(seed, ["https://any-other.com/z"])
    assert len(accepted) == 1


def test_extract_links_basic():
    html = '<a href="/rel">x</a><a href="https://ex.com/abs">y</a><a href="mailto:a@b.com">m</a><a href="#frag">f</a>'
    base = "https://ex.com/page"
    links = extract_links(html, base)
    assert len(links) == 2
    assert "https://ex.com/rel" in links
    assert "https://ex.com/abs" in links
    # 중복 제거 테스트
    html_dup = '<a href="/rel">x</a><a href="/rel">y</a>'
    links_dup = extract_links(html_dup, base)
    assert len(links_dup) == 1
    assert links_dup == ["https://ex.com/rel"]


def test_extract_links_empty():
    assert extract_links("", "https://ex.com") == []
    assert extract_links(None, "https://ex.com") == []


def test_parse_sitemap():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://ex.com/1</loc></url>
  <url><loc>https://ex.com/2</loc></url>
</urlset>"""
    urls = parse_sitemap(xml)
    assert len(urls) == 2
    assert "https://ex.com/1" in urls
    assert "https://ex.com/2" in urls

    assert parse_sitemap("") == []


def test_inmemory_queue_fifo():
    q = InMemoryFrontierQueue()
    assert q.pop() is None
    item1 = FrontierItem(url="https://ex.com/a", domain="ex.com", depth=0)
    item2 = FrontierItem(url="https://ex.com/b", domain="ex.com", depth=0)
    q.push(item1)
    q.push(item2)
    assert len(q) == 2
    assert q.pop() is item1
    assert len(q) == 1
    assert q.pop() is item2
    assert len(q) == 0
    assert q.pop() is None