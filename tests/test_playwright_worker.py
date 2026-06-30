import hashlib

from app.workers.playwright_worker import PlaywrightWorker, RenderResult


class _FakeRenderer:
    def __init__(self, *, html="<html><main>rendered</main></html>", screenshot_ref="shot-1", raise_exc=None):
        self.html = html
        self.screenshot_ref = screenshot_ref
        self.raise_exc = raise_exc
        self.calls = []

    def render(self, url, *, timeout=30.0, wait_until="networkidle", max_bytes=5000000):
        self.calls.append((url, wait_until, max_bytes))
        if self.raise_exc is not None:
            raise self.raise_exc
        hb = self.html.encode("utf-8")
        truncated = len(hb) > max_bytes
        if truncated:
            hb = hb[:max_bytes]
        html = hb.decode("utf-8", "ignore")
        return RenderResult(
            url=url,
            html=html,
            content_hash=hashlib.sha256(hb).hexdigest(),
            byte_size=len(hb),
            screenshot_ref=self.screenshot_ref,
            truncated=truncated,
        )


def test_supports():
    w = PlaywrightWorker(renderer=_FakeRenderer())
    assert w.supports("render_html", "text/html") is True
    assert w.supports("render_js", "x") is True
    assert w.supports("download_file", "application/pdf") is False


def test_precheck_missing_url():
    w = PlaywrightWorker(renderer=_FakeRenderer())
    result = w.precheck({"job_type": "render_html"})
    assert result["ok"] is False


def test_precheck_ok():
    w = PlaywrightWorker(renderer=_FakeRenderer())
    result = w.precheck({"url": "https://ex.com", "job_type": "render_html"})
    assert result["ok"] is True


def test_precheck_forbidden_login():
    w = PlaywrightWorker(renderer=_FakeRenderer())
    result = w.precheck({
        "url": "https://ex.com",
        "job_type": "render_html",
        "job_config": {"login": True},
    })
    assert result["ok"] is False
    assert result["reason"] == "forbidden_action:login"


def test_precheck_forbidden_captcha():
    w = PlaywrightWorker(renderer=_FakeRenderer())
    result = w.precheck({
        "url": "https://ex.com",
        "job_type": "render_html",
        "job_config": {"solve_captcha": True},
    })
    assert result["ok"] is False
    assert result["reason"] == "forbidden_action:solve_captcha"


def test_precheck_forbidden_falsy_ignored():
    w = PlaywrightWorker(renderer=_FakeRenderer())
    result = w.precheck({
        "url": "https://ex.com",
        "job_type": "render_html",
        "job_config": {"login": False},
    })
    assert result["ok"] is True


def test_execute_success():
    ff = _FakeRenderer(html="<html><main>hi</main></html>", screenshot_ref="S1")
    w = PlaywrightWorker(renderer=ff)
    res = w.execute({"url": "https://ex.com/p", "job_type": "render_html"})
    assert res["status"] == "succeeded"
    assert res["text"] == "<html><main>hi</main></html>"
    assert res["content_type"] == "text/html"
    assert res["screenshot_ref"] == "S1"
    expected_hash = hashlib.sha256(b"<html><main>hi</main></html>").hexdigest()
    assert res["content_hash"] == expected_hash
    assert res["byte_size"] == len(b"<html><main>hi</main></html>")


def test_execute_uses_wait_until_and_max_bytes():
    ff = _FakeRenderer()
    w = PlaywrightWorker(renderer=ff, wait_until="load")
    w.execute({
        "url": "https://ex.com",
        "job_type": "render_html",
        "job_config": {"max_bytes": 99},
    })
    assert ff.calls[0][1] == "load"
    assert ff.calls[0][2] == 99


def test_execute_failure():
    ff = _FakeRenderer(raise_exc=RuntimeError("render boom"))
    res = PlaywrightWorker(renderer=ff).execute({"url": "https://ex.com/x", "job_type": "render_js"})
    assert res["status"] == "failed"
    assert "render boom" in res["error"]
    assert res["text"] is None


def test_postcheck_ok():
    w = PlaywrightWorker(renderer=_FakeRenderer())
    result = w.postcheck({"status": "succeeded", "byte_size": 100})
    assert result["ok"] is True


def test_postcheck_render_failed():
    w = PlaywrightWorker(renderer=_FakeRenderer())
    result = w.postcheck({"status": "failed"})
    assert result["reason"] == "render_failed"


def test_postcheck_too_large():
    w = PlaywrightWorker(renderer=_FakeRenderer(), max_bytes=10)
    result = w.postcheck({"status": "succeeded", "byte_size": 11})
    assert result["reason"] == "too_large"


def test_runner_compatible_keys():
    res = PlaywrightWorker(renderer=_FakeRenderer()).execute({
        "url": "https://ex.com",
        "job_type": "render_html",
    })
    for k in ("status", "text", "content_hash", "byte_size", "url", "content_type"):
        assert k in res