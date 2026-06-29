"""API 요청/응답 계약 (설계서 §6). Pydantic 모델 — 핸들러 로직은 api/ 에서.

Sprint 0 범위: Source 등록 / Policy Check / Delete Request.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# --- §6.1 Source 등록 ---
class SourceRegisterRequest(BaseModel):
    url: str
    source_type: str
    discovery_method: str = "manual"
    intended_use: str = "pattern_extraction"
    notes: str | None = None
    org_id: str | None = None  # v2.1 멀티테넌시


class SourceRegisterResponse(BaseModel):
    source_id: str
    crawl_status: str
    license_status: str
    next_action: str


# --- §6.2 Policy Check ---
class PolicyCheckResponse(BaseModel):
    source_id: str
    robots_allowed: bool | None = None
    terms_review_status: str
    license_status: str
    crawl_status: str
    allowed_actions: list[str] = Field(default_factory=list)


# --- §6.5 Delete Request ---
class DeleteRequest(BaseModel):
    request_type: str
    requester: str
    requester_contact: str | None = None
    reason: str | None = None


class DeleteRequestResponse(BaseModel):
    source_id: str
    crawl_status: str
    status: str
