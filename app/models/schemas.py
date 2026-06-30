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


# --- §6.3 Crawl Job 생성 ---
class CrawlJobCreateRequest(BaseModel):
    source_id: str
    job_type: str = "fetch_html"
    priority: int = 100
    content_hash: str | None = None
    job_config: dict | None = None


class CrawlJobCreateResponse(BaseModel):
    job_id: str
    status: str
    created: bool


# --- §6.4 Pattern 승인/차단 ---
class PatternReviewRequest(BaseModel):
    reviewer_id: str
    reason: str | None = None


class PatternReviewResponse(BaseModel):
    pattern_id: str
    pattern_status: str


# --- 운영: 삭제 전파 / 재검증 ---
class ApplyDeleteRequest(BaseModel):
    request_id: str | None = None
    actor_id: str | None = None


class ApplyDeleteResponse(BaseModel):
    source_id: str
    patterns_blocked: int
    snapshots_blocked: int
    status: str


class RecheckRequest(BaseModel):
    result: str
    actor_id: str | None = None


class RecheckResponse(BaseModel):
    source_id: str
    action: str
    patterns_blocked: int
