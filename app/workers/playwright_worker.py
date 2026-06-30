"""Playwright Worker — 동적 렌더 worker (§8.3).

HTTPFetchWorker 와 **동일한 WebWorker 계약**(supports/precheck/execute/postcheck) +
주입형 renderer(운영 PlaywrightRenderer / 테스트 가짜). execute 출력은 http_worker 와
동일 형태("text"=렌더된 HTML)라 기존 runner.process_crawl_job 가 그대로 동작.

보안(§8.3): 로그인 입력·CAPTCHA·paywall 우회 금지(precheck 차단), 다운로드 자동실행 금지
(accept_downloads=False). SSRF 1차 차단은 상류 robots_checker. playwright 는 render 내부 지역 import.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.workers.base import WebWorker


@dataclass
class RenderResult:
    """동적 렌더 결과."""
    url: str
    html: str
    content_hash: str        # sha256 hex (html bytes)
    byte_size: int
    screenshot_ref: str | None = None
    truncated: bool = False


class PlaywrightRenderer:
    """실 동적 렌더(playwright sync). 보안: 로그인/CAPTCHA·paywall 우회·다운로드 자동실행 금지."""

    def render(
        self,
        url: str,
        *,
        timeout: float = 30.0,
        wait_until: str = "networkidle",
        max_bytes: int = 5000000,
    ) -> RenderResult:
        """playwright 로 URL 렌더 후 RenderResult. max_bytes 초과 시 절단."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=False)  # 다운로드 자동실행 금지
            page = context.new_page()
            page.goto(url, wait_until=wait_until, timeout=int(timeout * 1000))
            html = page.content()
            browser.close()

        html_bytes = html.encode("utf-8")
        truncated = len(html_bytes) > max_bytes
        if truncated:
            html_bytes = html_bytes[:max_bytes]
            html = html_bytes.decode("utf-8", "ignore")

        return RenderResult(
            url=url,
            html=html,
            content_hash=hashlib.sha256(html_bytes).hexdigest(),
            byte_size=len(html_bytes),
            screenshot_ref=None,
            truncated=truncated,
        )


# job_config 에 이 키가 truthy 로 있으면 precheck 거부(로그인/CAPTCHA/paywall 우회 금지).
_FORBIDDEN_CONFIG_KEYS = ("login", "credentials", "bypass_paywall", "solve_captcha")


class PlaywrightWorker(WebWorker):
    """Playwright 동적 렌더 워커. render_html/render_js 지원."""

    worker_name = "playwright"
    version = "1.0.0"
    SUPPORTED_JOB_TYPES = frozenset({"render_html", "render_js"})

    def __init__(self, *, renderer=None, max_bytes: int = 5000000, wait_until: str = "networkidle") -> None:
        """renderer 주입(없으면 PlaywrightRenderer()). max_bytes/wait_until 설정."""
        self.renderer = renderer if renderer is not None else PlaywrightRenderer()
        self.max_bytes = max_bytes
        self.wait_until = wait_until

    def supports(self, job_type: str, mime_type: str) -> bool:
        """job_type 지원 여부(mime 무시)."""
        return job_type in self.SUPPORTED_JOB_TYPES

    def precheck(self, job: dict[str, Any]) -> dict[str, Any]:
        """url 존재·job_type 지원·금지 액션(§8.3) 검증."""
        url = job.get("url")
        if not url or not isinstance(url, str):
            return {"ok": False, "reason": "missing_or_invalid_url"}

        if job.get("job_type") not in self.SUPPORTED_JOB_TYPES:
            return {"ok": False, "reason": f"unsupported_job_type:{job.get('job_type')}"}

        cfg = job.get("job_config") or {}
        for key in _FORBIDDEN_CONFIG_KEYS:
            if cfg.get(key):
                return {"ok": False, "reason": f"forbidden_action:{key}"}

        return {"ok": True, "reason": None}

    def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        """렌더 실행. RenderResult → http_worker 호환 dict. 예외 시 status=failed."""
        url = job["url"]
        cfg = job.get("job_config") or {}
        max_bytes = cfg.get("max_bytes", self.max_bytes)

        try:
            res = self.renderer.render(url, wait_until=self.wait_until, max_bytes=max_bytes)
        except Exception as e:  # noqa: BLE001 — 렌더 오류 전반 → failed 결과로 정규화
            return {
                "status": "failed",
                "url": url,
                "error": f"{type(e).__name__}: {e}",
                "text": None,
                "content_hash": None,
                "content_type": None,
                "byte_size": 0,
                "screenshot_ref": None,
                "truncated": False,
            }

        return {
            "status": "succeeded",
            "url": url,
            "text": res.html,
            "content_hash": res.content_hash,
            "content_type": "text/html",
            "byte_size": res.byte_size,
            "screenshot_ref": res.screenshot_ref,
            "truncated": res.truncated,
        }

    def postcheck(self, result: dict[str, Any]) -> dict[str, Any]:
        """결과 게이트: 실패/크기초과."""
        if result.get("status") == "failed":
            return {"ok": False, "reason": "render_failed"}
        if result.get("byte_size", 0) > self.max_bytes:
            return {"ok": False, "reason": "too_large"}
        return {"ok": True, "reason": None}