"""SQLAlchemy 2.0 ORM 매핑 — sql/001_init.sql 정본과 1:1 (Sprint 0).

⚠️ 컬럼명/타입/기본값은 sql/001_init.sql 과 정확히 일치해야 한다(스키마 정본은 SQL).
uuid PK 는 파이썬 uuid4 로 생성(default=uuid.uuid4).

Sprint 0 사용: web_sources / crawl_policies / web_audit_logs / web_delete_requests.
crawl_jobs / crawl_snapshots / web_patterns / brand_risk 는 매핑만(미사용 OK).

이식성 노트: 운영은 PostgreSQL(asyncpg). 테스트용 sqlite 호환을 위해
PG 전용 타입(UUID/JSONB/vector)은 portable 폴백을 갖는 타입으로 매핑한다.
- UUID  → PG: native UUID(as_uuid=True), 그 외: String(36) (uuid4 str)
- JSONB → PG: JSONB, 그 외: SQLAlchemy JSON
- vector(web_patterns.embedding) → 미사용이라 sqlite 에서도 안전한 Text 폴백
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator


class Base(DeclarativeBase):
    pass


class GUID(TypeDecorator):
    """플랫폼 독립 UUID. PostgreSQL 은 native UUID, 그 외(sqlite)는 CHAR(36)."""

    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


# JSONB(PG) → 그 외 JSON 폴백
JSONType = PG_JSONB().with_variant(JSON(), "sqlite")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


# ----------------------------------------------------------------------------
# 5.1 web_sources
# ----------------------------------------------------------------------------
class WebSource(Base):
    __tablename__ = "web_sources"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovered_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovery_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    crawl_status: Mapped[str] = mapped_column(Text, nullable=False)
    license_status: Mapped[str] = mapped_column(Text, nullable=False)
    robots_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    terms_review_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="not_reviewed", default="not_reviewed"
    )
    commercial_use_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="unknown", default="unknown"
    )
    pii_risk: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="unknown", default="unknown"
    )
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    near_dup_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        JSONType, nullable=False, server_default="{}", default=dict
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=func.now(),
        onupdate=func.now(),
    )


# ----------------------------------------------------------------------------
# 5.2 crawl_policies
# ----------------------------------------------------------------------------
class CrawlPolicy(Base):
    __tablename__ = "crawl_policies"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    robots_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    robots_snapshot_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    robots_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    allow_crawl: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    allow_file_download: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    allow_render: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    max_depth: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    max_pages_per_day: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="100", default=100
    )
    crawl_delay_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1000", default=1000
    )
    include_patterns: Mapped[list] = mapped_column(
        JSONType, nullable=False, server_default="[]", default=list
    )
    exclude_patterns: Mapped[list] = mapped_column(
        JSONType, nullable=False, server_default="[]", default=list
    )
    review_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="needs_review", default="needs_review"
    )
    reviewed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )


# ----------------------------------------------------------------------------
# 5.3 crawl_jobs (매핑만 — Sprint 0 미사용)
# ----------------------------------------------------------------------------
class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    source_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("web_sources.id"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="100", default=100
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="3", default=3
    )
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_config: Mapped[dict] = mapped_column(
        JSONType, nullable=False, server_default="{}", default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )


# ----------------------------------------------------------------------------
# 5.4 crawl_snapshots (매핑만 — Sprint 0 미사용)
# ----------------------------------------------------------------------------
class CrawlSnapshot(Base):
    __tablename__ = "crawl_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    source_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("web_sources.id"), nullable=False
    )
    crawl_job_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("crawl_jobs.id"), nullable=True
    )
    snapshot_type: Mapped[str] = mapped_column(Text, nullable=False)
    storage_ref: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )
    retention_policy: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="default", default="default"
    )
    access_level: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="restricted", default="restricted"
    )


# ----------------------------------------------------------------------------
# 5.5 web_patterns (매핑만 — Sprint 0 미사용)
#   embedding(vector) 은 Sprint 0 미사용이라 Text 폴백으로 매핑(sqlite 호환).
# ----------------------------------------------------------------------------
class WebPattern(Base):
    __tablename__ = "web_patterns"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("web_sources.id"), nullable=False
    )
    pattern_type: Mapped[str] = mapped_column(Text, nullable=False)
    abstraction_level: Mapped[str] = mapped_column(Text, nullable=False)
    original_reuse_risk: Mapped[str] = mapped_column(Text, nullable=False)
    reuse_subscores: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    reuse_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    reuse_hardrule: Mapped[str | None] = mapped_column(Text, nullable=True)
    recon_test_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    feature_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    # vector → Sprint 0 미사용. Text 폴백(운영 PG 에선 별도 마이그레이션으로 vector 사용).
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    license_status: Mapped[str] = mapped_column(Text, nullable=False)
    pii_status: Mapped[str] = mapped_column(Text, nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    pattern_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="built", default="built"
    )
    version: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="1.0.0", default="1.0.0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=func.now(),
        onupdate=func.now(),
    )


# ----------------------------------------------------------------------------
# 5.6 web_audit_logs
# ----------------------------------------------------------------------------
class WebAuditLog(Base):
    __tablename__ = "web_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    actor_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    pattern_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    before_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    after_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )


# ----------------------------------------------------------------------------
# 5.7 web_delete_requests
# ----------------------------------------------------------------------------
class WebDeleteRequest(Base):
    __tablename__ = "web_delete_requests"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("web_sources.id"), nullable=True
    )
    requester: Mapped[str | None] = mapped_column(Text, nullable=True)
    requester_contact: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_type: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="received", default="received"
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)


# ----------------------------------------------------------------------------
# v2.1 P-5 brand_risk (매핑만 — Sprint 0 미사용)
# ----------------------------------------------------------------------------
class BrandRisk(Base):
    __tablename__ = "brand_risk"

    domain: Mapped[str] = mapped_column(Text, primary_key=True)
    brand_risk: Mapped[float] = mapped_column(
        Numeric, nullable=False, server_default="0.5", default=0.5
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=func.now(),
        onupdate=func.now(),
    )
