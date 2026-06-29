"""canonicalize_url / extract_domain 단위테스트 (외부 의존 없음)."""
from app.policy.url_canon import canonicalize_url, extract_domain


def test_scheme_and_host_lowercase():
    assert canonicalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"


def test_scheme_added_when_missing():
    assert canonicalize_url("example.com/x") == "https://example.com/x"


def test_fragment_removed():
    assert canonicalize_url("https://example.com/p#section") == "https://example.com/p"


def test_query_sorted():
    assert (
        canonicalize_url("https://example.com/p?b=2&a=1&c=3")
        == "https://example.com/p?a=1&b=2&c=3"
    )


def test_empty_query_removed():
    assert canonicalize_url("https://example.com/p?") == "https://example.com/p"


def test_trailing_slash_removed_but_root_kept():
    assert canonicalize_url("https://example.com/dir/") == "https://example.com/dir"
    assert canonicalize_url("https://example.com/") == "https://example.com/"
    assert canonicalize_url("https://example.com") == "https://example.com/"


def test_default_port_removed():
    assert canonicalize_url("http://example.com:80/p") == "http://example.com/p"
    assert canonicalize_url("https://example.com:443/p") == "https://example.com/p"


def test_nondefault_port_kept():
    assert canonicalize_url("https://example.com:8443/p") == "https://example.com:8443/p"


def test_blank_value_preserved():
    assert canonicalize_url("https://example.com/p?flag=") == "https://example.com/p?flag="


def test_extract_domain():
    assert extract_domain("https://Example.com:8443/x?y=1") == "example.com"
    assert extract_domain("sub.example.com/path") == "sub.example.com"


def test_empty_url_raises():
    import pytest

    with pytest.raises(ValueError):
        canonicalize_url("   ")
