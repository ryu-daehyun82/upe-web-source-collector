"""상태기계 정의 (설계서 §4). 계약(enum) — 전이 로직은 policy/license_state.py 등에서.

3종 상태기계: Source / CrawlJob / Pattern. 차단 사유·라이선스·PII·reuse 등급 포함.
"""
from __future__ import annotations

from enum import Enum


class SourceStatus(str, Enum):
    discovered = "discovered"
    policy_pending = "policy_pending"
    allowed_metadata_only = "allowed_metadata_only"
    allowed_crawl = "allowed_crawl"
    crawled = "crawled"
    parsed = "parsed"
    pattern_built = "pattern_built"
    approved_for_pattern_use = "approved_for_pattern_use"
    blocked = "blocked"
    delete_requested = "delete_requested"
    deleted = "deleted"


class BlockedReason(str, Enum):
    by_robots = "blocked_by_robots"
    by_terms = "blocked_by_terms"
    sensitive = "blocked_sensitive"
    license = "blocked_license"
    manual = "blocked_manual"


class CrawlJobStatus(str, Enum):
    queued = "queued"
    policy_checking = "policy_checking"
    ready = "ready"
    running = "running"
    succeeded = "succeeded"
    failed_retryable = "failed_retryable"
    failed_terminal = "failed_terminal"
    cancelled = "cancelled"


class PatternStatus(str, Enum):
    built = "built"
    abstraction_checked = "abstraction_checked"
    reuse_risk_scored = "reuse_risk_scored"
    approved = "approved"
    blocked = "blocked"
    deprecated = "deprecated"


class LicenseStatus(str, Enum):
    unknown = "unknown"
    allowed = "allowed"
    conditional = "conditional"          # = conditional_approved
    conditional_approved = "conditional_approved"
    blocked = "blocked"


class PiiStatus(str, Enum):
    unknown = "unknown"
    clean = "clean"
    redacted = "redacted"
    sensitive = "sensitive"
    blocked = "blocked"


class ReuseRisk(str, Enum):
    low = "low"          # 0.00~0.30
    medium = "medium"    # 0.31~0.60
    high = "high"        # 0.61~0.80
    blocked = "blocked"  # 0.81~1.00


# 운영 사용 가능 조건 (설계서 §4.3) — 게이트에서 참조
OPERATIONAL_SOURCE_STATES = {
    SourceStatus.parsed,
    SourceStatus.pattern_built,
    SourceStatus.approved_for_pattern_use,
}
OPERATIONAL_LICENSE_STATES = {LicenseStatus.allowed, LicenseStatus.conditional_approved}
OPERATIONAL_PII_STATES = {PiiStatus.clean, PiiStatus.redacted}
OPERATIONAL_REUSE_STATES = {ReuseRisk.low, ReuseRisk.medium}
