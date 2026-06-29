"""공통 Worker 인터페이스 (설계서 §8.1). 계약(ABC) — 구현체는 Sprint 1+.

supports → precheck → execute → postcheck 4단계 생명주기.
구현체(코딩풀): HTTPFetchWorker / PlaywrightWorker / ParserWorker.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class WebWorker(ABC):
    worker_name: str
    version: str

    @abstractmethod
    def supports(self, job_type: str, mime_type: str) -> bool:
        """이 워커가 해당 job_type/mime 처리 가능한지."""
        raise NotImplementedError

    @abstractmethod
    def precheck(self, job: dict[str, Any]) -> dict[str, Any]:
        """실행 전 검증(size/content-type/권한 등). 차단 시 사유 반환."""
        raise NotImplementedError

    @abstractmethod
    def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        """수집/파싱 본체. snapshot/feature 산출."""
        raise NotImplementedError

    @abstractmethod
    def postcheck(self, result: dict[str, Any]) -> dict[str, Any]:
        """결과 검증(hash/pii/quality). 패턴 저장 전 게이트."""
        raise NotImplementedError
