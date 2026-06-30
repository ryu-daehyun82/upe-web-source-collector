"""PDF Parse Worker (§8.4) — fetch(바이너리) + parse_pdf.

HTTPFetchWorker 와 **동일한 WebWorker 계약**(supports/precheck/execute/postcheck).
PDF 는 바이너리라 fetch(주입형) 후 parse_pdf 로 feature 를 산출하고, execute 결과의
"feature" 에 raw_feature 를 담는다. runner.process_crawl_job 는 result["feature"] 를
우선 사용하므로 텍스트형(HTTP/Playwright)과 동일 파이프라인으로 흐른다.
"""
from __future__ import annotations

from typing import Any

from app.workers.base import WebWorker
from app.workers.http_worker import HttpxFetcher
from app.workers.pdf_parser import parse_pdf


class PDFParseWorker(WebWorker):
    """PDF 다운로드+파싱 워커. HTTP 로 PDF 를 내려받아 parse_pdf 로 feature 산출."""

    worker_name = "pdf_parse"
    version = "1.0.0"
    SUPPORTED_JOB_TYPES = frozenset({"parse_pdf", "download_pdf"})

    def __init__(self, *, fetcher=None, parser=parse_pdf, max_bytes: int = 52428800) -> None:
        """fetcher 주입(없으면 HttpxFetcher()). parser 주입(기본 parse_pdf). max_bytes 상한."""
        self.fetcher = fetcher if fetcher is not None else HttpxFetcher()
        self.parser = parser
        self.max_bytes = max_bytes

    def supports(self, job_type: str, mime_type: str) -> bool:
        """지원 job_type 인지."""
        return job_type in self.SUPPORTED_JOB_TYPES

    def precheck(self, job: dict[str, Any]) -> dict[str, Any]:
        """url 존재·job_type 지원·max_bytes 양수 검증."""
        url = job.get("url")
        if not url or not isinstance(url, str) or not url.strip():
            return {"ok": False, "reason": "missing_or_invalid_url"}
        if not self.supports(job.get("job_type"), ""):
            return {"ok": False, "reason": "unsupported_job_type"}
        cfg = job.get("job_config") or {}
        max_bytes = cfg.get("max_bytes", self.max_bytes)
        if max_bytes <= 0:
            return {"ok": False, "reason": "invalid_max_bytes"}
        return {"ok": True, "reason": None}

    def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        """PDF fetch 후 parse_pdf 로 feature 산출. 예외 시 status=failed."""
        url = job["url"]
        cfg = job.get("job_config") or {}
        max_bytes = cfg.get("max_bytes", self.max_bytes)

        try:
            res = self.fetcher.fetch(url, max_bytes=max_bytes)
        except Exception as e:  # noqa: BLE001 — fetch 오류 전반 → failed 정규화
            return {
                "status": "failed",
                "url": url,
                "error": f"{type(e).__name__}: {e}",
                "feature": None,
                "content_hash": None,
                "content_type": None,
                "byte_size": 0,
                "text": None,
                "truncated": False,
            }

        feature = self.parser(res.content, url=url)
        return {
            "status": "succeeded",
            "url": url,
            "feature": feature,
            "text": None,
            "content_hash": res.content_hash,
            "content_type": res.content_type,
            "byte_size": res.byte_size,
            "truncated": res.truncated,
            "page_count": feature.get("page_count"),
            "encrypted": feature.get("encrypted"),
        }

    def postcheck(self, result: dict[str, Any]) -> dict[str, Any]:
        """결과 게이트: 실패/크기초과/암호화 PDF."""
        if result.get("status") == "failed":
            return {"ok": False, "reason": "fetch_failed"}
        if result.get("byte_size", 0) > self.max_bytes:
            return {"ok": False, "reason": "too_large"}
        if result.get("encrypted"):
            return {"ok": False, "reason": "encrypted_pdf"}
        return {"ok": True, "reason": None}