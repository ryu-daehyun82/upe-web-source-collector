"""멱등 잡 생성 모듈 (v2.1 P-7 / §2).

Kafka 재시도·job retry 중복을 idempotency_key 로 막는다. DDL 의 crawl_jobs.idempotency_key
+ unique index ux_crawl_jobs_idem 가 운영 DB 단의 멱등을 보장하고, 여기서는 select-first
+ IntegrityError 폴백으로 애플리케이션 단 멱등을 구현한다.

idempotency_key = blake2b(source_id | job_type | content_hash).
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import CrawlJobStatus
from app.models.tables import CrawlJob


def _as_uuid(value: uuid.UUID | str) -> uuid.UUID:
    """source_id 를 uuid.UUID 로 정규화."""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(value)


def make_idempotency_key(
    source_id: uuid.UUID | str,
    job_type: str,
    content_hash: str | None = None,
) -> str:
    """안정적 멱등 키. blake2b(f"{source_id}|{job_type}|{content_hash or ''}") 32 hex.

    source_id 는 uuid.UUID/str 모두 허용 → str() 정규화. 결정적(같은 입력→같은 키).
    """
    raw = f"{str(source_id)}|{job_type}|{content_hash or ''}"
    return hashlib.blake2b(raw.encode(), digest_size=16).hexdigest()


async def enqueue_crawl_job(
    session: AsyncSession,
    *,
    source_id: uuid.UUID | str,
    url: str,
    job_type: str,
    content_hash: str | None = None,
    priority: int = 100,
    job_config: dict[str, Any] | None = None,
) -> tuple[CrawlJob, bool]:
    """멱등 잡 생성. 반환 (job, created): created=True 새로 생성, False 기존 반환.

    동일 (source_id, job_type, content_hash) 재호출은 멱등키로 하나의 잡만 만든다.
    """
    key = make_idempotency_key(source_id, job_type, content_hash)

    existing = await session.scalar(
        select(CrawlJob).where(CrawlJob.idempotency_key == key)
    )
    if existing is not None:
        return existing, False

    source_uuid = _as_uuid(source_id)
    job = CrawlJob(
        source_id=source_uuid,
        url=url,
        job_type=job_type,
        status=CrawlJobStatus.queued.value,
        priority=priority,
        idempotency_key=key,
        job_config=job_config or {},
    )
    session.add(job)

    try:
        await session.flush()
        return job, True
    except IntegrityError:
        # 동시성 레이스: 다른 워커가 먼저 같은 키로 생성 → 롤백 후 기존 반환.
        await session.rollback()
        existing = await session.scalar(
            select(CrawlJob).where(CrawlJob.idempotency_key == key)
        )
        return existing, False


async def get_job_by_idempotency_key(session: AsyncSession, key: str) -> CrawlJob | None:
    """멱등키로 잡 조회."""
    return await session.scalar(
        select(CrawlJob).where(CrawlJob.idempotency_key == key)
    )