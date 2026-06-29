"""HTTP Fetch Worker (§8.2) — worker 본체.

WebWorker 계약(supports→precheck→execute→postcheck) 구현 + 주입형 fetcher.
운영은 HttpxFetcher(실 httpx), 테스트는 가짜 fetcher 주입. SSRF 1차 차단은 상류
robots_checker 책임. httpx 는 HttpxFetcher.fetch 내부 지역 import.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.workers.base import WebWorker


@dataclass
class FetchResult:
    """HTTP fetch 결과."""
    url: str
    status_code: int
    content: bytes
    content_type: str | None
    content_hash: str        # sha256 hex
    byte_size: int
    truncated: bool = False


class HttpxFetcher:
    """실 HTTP fetch(httpx) 어댑터. 운영용. SSRF 는 상류 robots_checker 가 1차 차단."""

    def fetch(self, url: str, *, max_bytes: int = 52428800, timeout: float = 30.0) -> FetchResult:
        """httpx 로 URL GET 후 FetchResult 반환. max_bytes 초과 시 절단(truncated)."""
        import httpx

        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.get(url, headers={"User-Agent": "UPE-Collector"})
            content = resp.content
            truncated = len(content) > max_bytes
            if truncated:
                content = content[:max_bytes]
            return FetchResult(
                url=url,
                status_code=resp.status_code,
                content=content,
                content_type=resp.headers.get("content-type"),
                content_hash=hashlib.sha256(content).hexdigest(),
                byte_size=len(content),
                truncated=truncated,
            )


def _is_text_content(content_type: str | None) -> bool:
    """content_type 이 텍스트 기반인지. None=False. text/ 시작 또는 html/json/xml 포함."""
    if content_type is None:
        return False
    ct = content_type.lower()
    if ct.startswith("text/"):
        return True
    return any(marker in ct for marker in ("html", "json", "xml"))


class HTTPFetchWorker(WebWorker):
    """HTTP 파일/HTML fetch 워커. 생명주기: supports → precheck → execute → postcheck."""

    worker_name = "http_fetch"
    version = "1.0.0"
    SUPPORTED_JOB_TYPES = frozenset({"download_file", "fetch_html"})

    def __init__(self, *, fetcher=None, max_bytes: int = 52428800, allowed_content_types=None) -> None:
        """fetcher 주입(없으면 HttpxFetcher()). allowed_content_types: set|None(None=전체 허용)."""
        self.fetcher = fetcher if fetcher is not None else HttpxFetcher()
        self.max_bytes = max_bytes
        self.allowed_content_types = allowed_content_types

    def supports(self, job_type: str, mime_type: str) -> bool:
        """지원 job_type 인지."""
        return job_type in self.SUPPORTED_JOB_TYPES

    def precheck(self, job: dict[str, Any]) -> dict[str, Any]:
        """url 존재·job_type 지원·max_bytes 양수 검증."""
        url = job.get("url")
        if not url or not isinstance(url, str) or not url.strip():
            return {"ok": False, "reason": "missing_or_invalid_url"}
        if not self.supports(job.get("job_type", ""), job.get("mime_type", "")):
            return {"ok": False, "reason": "unsupported_job_type"}
        cfg = job.get("job_config", {})
        if cfg:
            mb = cfg.get("max_bytes")
            if mb is not None and mb <= 0:
                return {"ok": False, "reason": "invalid_max_bytes"}
        return {"ok": True, "reason": None}

    def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        """HTTP fetch 수행. 텍스트 콘텐츠면 디코드 포함. 예외 시 status=failed."""
        url = job["url"]
        cfg = job.get("job_config", {})
        max_bytes = cfg.get("max_bytes", self.max_bytes)
        try:
            res = self.fetcher.fetch(url, max_bytes=max_bytes)
        except Exception as e:  # noqa: BLE001 — fetch 오류 전반 → failed 결과로 정규화
            return {
                "status": "failed",
                "url": url,
                "error": f"{type(e).__name__}: {e}",
                "content_hash": None,
                "content_type": None,
                "byte_size": 0,
                "text": None,
                "truncated": False,
            }
        text = res.content.decode("utf-8", "replace") if _is_text_content(res.content_type) else None
        return {
            "status": "succeeded",
            "url": url,
            "content_hash": res.content_hash,
            "content_type": res.content_type,
            "byte_size": res.byte_size,
            "status_code": res.status_code,
            "text": text,
            "truncated": res.truncated,
        }

    def postcheck(self, result: dict[str, Any]) -> dict[str, Any]:
        """결과 게이트: 실패/크기초과/콘텐츠타입 차단."""
        if result.get("status") == "failed":
            return {"ok": False, "reason": "fetch_failed"}
        if result.get("byte_size", 0) > self.max_bytes:
            return {"ok": False, "reason": "too_large"}
        if self.allowed_content_types is not None:
            content_type = result.get("content_type")
            if content_type:
                passed = any(content_type.startswith(a) for a in self.allowed_content_types)
                if not passed:
                    return {"ok": False, "reason": "content_type_blocked"}
        return {"ok": True, "reason": None}