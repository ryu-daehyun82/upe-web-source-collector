"""범용 파일 파서 워커 (§8.4) — parser·job_types 주입형.

PDFParseWorker 와 동일 컨셉이나 parser(bytes→feature)와 지원 job_type 을 주입받아
ppt/image/video 등 임의 바이너리 파서를 동일 WebWorker 계약으로 감싼다.
execute 결과의 "feature" 를 runner.process_crawl_job 이 거버넌스에 넘긴다.
"""
from __future__ import annotations

from typing import Any

from app.workers.base import WebWorker
from app.workers.http_worker import HttpxFetcher


class FileParseWorker(WebWorker):
    """주입형 parser 로 바이너리를 fetch+parse 하는 범용 워커(PDF/PPT/Image/Video 재사용)."""

    version = "1.0.0"

    def __init__(
        self,
        *,
        parser,
        job_types: set[str] | frozenset[str],
        worker_name: str = "file_parse",
        fetcher=None,
        max_bytes: int = 52428800,
    ) -> None:
        """parser: callable(bytes, *, url=None)->dict. job_types: 지원 집합. fetcher 없으면 HttpxFetcher."""
        self.parser = parser
        self.job_types = frozenset(job_types)
        self.worker_name = worker_name
        self.fetcher = fetcher if fetcher is not None else HttpxFetcher()
        self.max_bytes = max_bytes

    def supports(self, job_type: str, mime_type: str) -> bool:
        """지원 job_type 인지."""
        return job_type in self.job_types

    def precheck(self, job: dict[str, Any]) -> dict[str, Any]:
        """url 존재·job_type 지원·max_bytes 양수 검증."""
        url = job.get("url")
        if not url or not isinstance(url, str):
            return {"ok": False, "reason": "missing_or_invalid_url"}
        if job.get("job_type") not in self.job_types:
            return {"ok": False, "reason": "unsupported_job_type"}
        cfg = job.get("job_config") or {}
        max_bytes = cfg.get("max_bytes", self.max_bytes)
        if not isinstance(max_bytes, (int, float)) or max_bytes <= 0:
            return {"ok": False, "reason": "invalid_max_bytes"}
        return {"ok": True, "reason": None}

    def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        """fetch 후 주입 parser 로 feature 산출. 예외 시 status=failed."""
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
            "layout_type": feature.get("layout_type"),
        }

    def postcheck(self, result: dict[str, Any]) -> dict[str, Any]:
        """결과 게이트: 실패/크기초과."""
        if result.get("status") == "failed":
            return {"ok": False, "reason": "fetch_failed"}
        if result.get("byte_size", 0) > self.max_bytes:
            return {"ok": False, "reason": "too_large"}
        return {"ok": True, "reason": None}