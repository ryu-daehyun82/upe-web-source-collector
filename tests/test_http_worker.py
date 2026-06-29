import hashlib

from app.workers.http_worker import HTTPFetchWorker, FetchResult, _is_text_content


class _FakeFetcher:
    def __init__(self, *, content=b"<html>hi</html>", content_type="text/html; charset=utf-8", status_code=200, raise_exc=None):
        self.content = content
        self.content_type = content_type
        self.status_code = status_code
        self.raise_exc = raise_exc
        self.calls = []

    def fetch(self, url, *, max_bytes=52428800, timeout=30.0):
        self.calls.append((url, max_bytes))
        if self.raise_exc is not None:
            raise self.raise_exc
        content = self.content[:max_bytes]
        truncated = len(self.content) > max_bytes
        return FetchResult(
            url=url, status_code=self.status_code, content=content,
            content_type=self.content_type,
            content_hash=hashlib.sha256(content).hexdigest(),
            byte_size=len(content), truncated=truncated,
        )


class TestSupports:
    def test_supports_download_file(self):
        w = HTTPFetchWorker(fetcher=_FakeFetcher())
        assert w.supports("download_file", "application/pdf") is True

    def test_supports_fetch_html(self):
        w = HTTPFetchWorker(fetcher=_FakeFetcher())
        assert w.supports("fetch_html", "text/html") is True

    def test_supports_render_js(self):
        w = HTTPFetchWorker(fetcher=_FakeFetcher())
        assert w.supports("render_js", "text/html") is False


class TestIsTextContent:
    def test_text_html(self):
        assert _is_text_content("text/html; charset=utf-8") is True

    def test_application_pdf(self):
        assert _is_text_content("application/pdf") is False

    def test_application_json(self):
        assert _is_text_content("application/json") is True

    def test_none(self):
        assert _is_text_content(None) is False

    def test_application_xml(self):
        assert _is_text_content("application/xml") is True


class TestPrecheck:
    def test_missing_url(self):
        w = HTTPFetchWorker(fetcher=_FakeFetcher())
        result = w.precheck({"job_type": "fetch_html"})
        assert result["ok"] is False

    def test_unsupported_job_type(self):
        w = HTTPFetchWorker(fetcher=_FakeFetcher())
        result = w.precheck({"url": "https://ex.com", "job_type": "render_js"})
        assert result["ok"] is False
        assert result["reason"] == "unsupported_job_type"

    def test_ok(self):
        w = HTTPFetchWorker(fetcher=_FakeFetcher())
        result = w.precheck({"url": "https://ex.com/x", "job_type": "download_file"})
        assert result["ok"] is True

    def test_bad_max_bytes(self):
        w = HTTPFetchWorker(fetcher=_FakeFetcher())
        result = w.precheck({"url": "https://ex.com", "job_type": "fetch_html", "job_config": {"max_bytes": 0}})
        assert result["ok"] is False


class TestExecute:
    def test_success_html_text(self):
        ff = _FakeFetcher(content=b"<html>body</html>", content_type="text/html")
        w = HTTPFetchWorker(fetcher=ff)
        res = w.execute({"url": "https://ex.com/p", "job_type": "fetch_html"})
        assert res["status"] == "succeeded"
        assert res["text"] == "<html>body</html>"
        assert res["content_hash"] == hashlib.sha256(b"<html>body</html>").hexdigest()
        assert res["byte_size"] == len(b"<html>body</html>")

    def test_binary_no_text(self):
        ff = _FakeFetcher(content=b"%PDF-1.4 ...", content_type="application/pdf")
        res = HTTPFetchWorker(fetcher=ff).execute({"url": "https://ex.com/f.pdf", "job_type": "download_file"})
        assert res["status"] == "succeeded"
        assert res["text"] is None
        assert res["content_type"] == "application/pdf"

    def test_failure(self):
        ff = _FakeFetcher(raise_exc=RuntimeError("boom"))
        res = HTTPFetchWorker(fetcher=ff).execute({"url": "https://ex.com/x", "job_type": "fetch_html"})
        assert res["status"] == "failed"
        assert "boom" in res["error"]
        assert res["content_hash"] is None

    def test_passes_max_bytes(self):
        ff = _FakeFetcher(content=b"0123456789")
        w = HTTPFetchWorker(fetcher=ff)
        res = w.execute({"url": "https://ex.com", "job_type": "fetch_html", "job_config": {"max_bytes": 4}})
        assert ff.calls[0][1] == 4
        assert res["truncated"] is True
        assert res["byte_size"] == 4


class TestPostcheck:
    def test_ok(self):
        w = HTTPFetchWorker(fetcher=_FakeFetcher())
        result = w.postcheck({"status": "succeeded", "byte_size": 100, "content_type": "text/html"})
        assert result["ok"] is True

    def test_failed_status(self):
        w = HTTPFetchWorker(fetcher=_FakeFetcher())
        result = w.postcheck({"status": "failed"})
        assert result["reason"] == "fetch_failed"

    def test_too_large(self):
        w = HTTPFetchWorker(fetcher=_FakeFetcher(), max_bytes=10)
        result = w.postcheck({"status": "succeeded", "byte_size": 11, "content_type": "text/html"})
        assert result["reason"] == "too_large"

    def test_content_type_blocked(self):
        w = HTTPFetchWorker(fetcher=_FakeFetcher(), allowed_content_types={"application/pdf"})
        result = w.postcheck({"status": "succeeded", "byte_size": 5, "content_type": "text/html"})
        assert result["reason"] == "content_type_blocked"

    def test_content_type_allowed(self):
        w = HTTPFetchWorker(fetcher=_FakeFetcher(), allowed_content_types={"application/pdf"})
        result = w.postcheck({"status": "succeeded", "byte_size": 5, "content_type": "application/pdf"})
        assert result["ok"] is True