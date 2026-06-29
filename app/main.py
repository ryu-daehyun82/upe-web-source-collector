"""UPE Web Source Collector — FastAPI 앱 골격 (Sprint 0).

라우터 결합만. 핸들러 로직은 api/web_sources.py (코딩풀 구현 위임).
"""
from __future__ import annotations

from fastapi import FastAPI

from app.api import web_sources

app = FastAPI(
    title="UPE Web Source Collector",
    version="0.1.0-sprint0",
    description="웹 패턴 거버넌스 파이프라인 — Policy-first 골격",
)

app.include_router(web_sources.router, prefix="/api/v1", tags=["web-sources"])


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "sprint": 0}
