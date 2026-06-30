import hashlib

from app.workers.pdf_worker import PDFParseWorker
from app.workers.http_worker import FetchResult


class _FakeFetcher:
    def __init__(self, *, content=b"%PDF-1.4 /Type /Page /Type /Page /Font %%EOF",
                 content_type="application/pdf", raise_exc=None):
        self.content = content
        self.content_type = content_type
        self.raise_exc = raise_exc
        self.calls = []

    def fetch(self, url, *, max_bytes=52428800, timeout=30.0):
        self.calls.append((url, max_bytes))
        if self.raise_exc is not None:
            raise self.raise_exc
        c = self.content[:max_bytes]
        return FetchResult(url=url, status_code=200, content=c, content_type=self.content_type,
                           content_hash=hashlib.sha256(c).hexdigest(), byte_size=len(c),
                           truncated=len(self.content) > max_bytes)


def test_supports():
    w = PDFParseWorker(fetcher=_FakeFetcher())
    assert w.supports("parse_pdf", "application/pdf") is True
    assert w.supports("download_pdf", "x") is True
    assert w.supports("fetch_html", "text/html") is False


def test_precheck_missing_url():
    w = PDFParseWorker(fetcher=_FakeFetcher())
    assert w.precheck({"job_type": "parse_pdf"})["ok"] is False


def test_precheck_ok():
    w = PDFParseWorker(fetcher=_FakeFetcher())
    assert w.precheck({"url": "https://ex.com/d.pdf", "job_type": "parse_pdf"})["ok"] is True


def test_precheck_bad_max_bytes():
    w = PDFParseWorker(fetcher=_FakeFetcher())
    assert w.precheck({"url": "https://ex.com/d.pdf", "job_type": "parse_pdf", "job_config": {"max_bytes": 0}})["ok"] is False


def test_execute_success():
    ff = _FakeFetcher()
    res = PDFParseWorker(fetcher=ff).execute({"url": "https://ex.com/d.pdf", "job_type": "parse_pdf"})
    assert res["status"] == "succeeded"
    assert res["text"] is None
    assert isinstance(res["feature"], dict)
    assert res["feature"]["layout_type"] == "document"
    assert res["feature"]["page_count"] == 2
    assert res["page_count"] == 2
    assert "content_hash" in res
    assert "byte_size" in res


def test_execute_failure():
    ff = _FakeFetcher(raise_exc=RuntimeError("dl fail"))
    res = PDFParseWorker(fetcher=ff).execute({"url": "https://ex.com/x.pdf", "job_type": "parse_pdf"})
    assert res["status"] == "failed"
    assert res["feature"] is None
    assert "dl fail" in res["error"]


def test_postcheck_ok():
    w = PDFParseWorker(fetcher=_FakeFetcher())
    assert w.postcheck({"status": "succeeded", "byte_size": 100, "encrypted": False})["ok"] is True


def test_postcheck_failed():
    w = PDFParseWorker(fetcher=_FakeFetcher())
    assert w.postcheck({"status": "failed"})["reason"] == "fetch_failed"


def test_postcheck_too_large():
    w = PDFParseWorker(fetcher=_FakeFetcher(), max_bytes=10)
    assert w.postcheck({"status": "succeeded", "byte_size": 11})["reason"] == "too_large"


def test_postcheck_encrypted():
    w = PDFParseWorker(fetcher=_FakeFetcher())
    assert w.postcheck({"status": "succeeded", "byte_size": 5, "encrypted": True})["reason"] == "encrypted_pdf"


def test_runner_compatible_feature_key():
    ff = _FakeFetcher()
    res = PDFParseWorker(fetcher=ff).execute({"url": "https://ex.com/d.pdf", "job_type": "parse_pdf"})
    assert "feature" in res
    assert "status" in res
    assert "url" in res
