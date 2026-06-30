"""crawl_job 상태 전이 영속화 (§4.2).

CrawlJobStatus 상태기계의 **검증된 전이** + 타임스탬프(started/finished)/에러/재시도
카운트 영속화. 허용되지 않은 전이는 InvalidTransition. now 주입으로 테스트 결정성 확보.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import CrawlJobStatus
from app.models.tables import CrawlJob


# 허용 전이(§4.2). queued→running 직행도 허용(단순 워커 경로).
ALLOWED_TRANSITIONS: dict[CrawlJobStatus, frozenset[CrawlJobStatus]] = {
    CrawlJobStatus.queued: frozenset({CrawlJobStatus.policy_checking, CrawlJobStatus.ready, CrawlJobStatus.running, CrawlJobStatus.cancelled}),
    CrawlJobStatus.policy_checking: frozenset({CrawlJobStatus.ready, CrawlJobStatus.failed_terminal, CrawlJobStatus.cancelled}),
    CrawlJobStatus.ready: frozenset({CrawlJobStatus.running, CrawlJobStatus.cancelled}),
    CrawlJobStatus.running: frozenset({CrawlJobStatus.succeeded, CrawlJobStatus.failed_retryable, CrawlJobStatus.failed_terminal, CrawlJobStatus.cancelled}),
    CrawlJobStatus.failed_retryable: frozenset({CrawlJobStatus.queued, CrawlJobStatus.running, CrawlJobStatus.failed_terminal, CrawlJobStatus.cancelled}),
    CrawlJobStatus.succeeded: frozenset(),
    CrawlJobStatus.failed_terminal: frozenset(),
    CrawlJobStatus.cancelled: frozenset(),
}

TERMINAL_STATES: frozenset[CrawlJobStatus] = frozenset({
    CrawlJobStatus.succeeded, CrawlJobStatus.failed_terminal, CrawlJobStatus.cancelled,
})


class InvalidTransition(Exception):
    """허용되지 않은 상태 전이."""


def can_transition(frm: CrawlJobStatus, to: CrawlJobStatus) -> bool:
    """frm → to 전이가 허용되는지."""
    return to in ALLOWED_TRANSITIONS.get(frm, frozenset())


async def transition_job(
    session: AsyncSession,
    job: CrawlJob,
    to_status: CrawlJobStatus,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    now: datetime | None = None,
) -> CrawlJob:
    """job.status 를 to_status 로 전이·영속화. 허용 안 되면 InvalidTransition.

    running → started_at, 터미널 → finished_at, failed_retryable → attempt_count+1,
    error_code/error_message 주어지면 설정. flush 까지.
    """
    frm = CrawlJobStatus(job.status)
    if not can_transition(frm, to_status):
        raise InvalidTransition(f"{frm.value} -> {to_status.value}")

    ts = now or datetime.now(timezone.utc)
    job.status = to_status.value

    if to_status == CrawlJobStatus.running:
        job.started_at = ts
    if to_status in TERMINAL_STATES:
        job.finished_at = ts
    if to_status == CrawlJobStatus.failed_retryable:
        job.attempt_count = (job.attempt_count or 0) + 1

    if error_code is not None:
        job.error_code = error_code
    if error_message is not None:
        job.error_message = error_message

    await session.flush()
    return job


async def mark_running(session: AsyncSession, job: CrawlJob, *, now: datetime | None = None) -> CrawlJob:
    """running 으로 전이."""
    return await transition_job(session, job, CrawlJobStatus.running, now=now)


async def mark_succeeded(session: AsyncSession, job: CrawlJob, *, now: datetime | None = None) -> CrawlJob:
    """succeeded 로 전이."""
    return await transition_job(session, job, CrawlJobStatus.succeeded, now=now)


async def mark_failed_retryable(
    session: AsyncSession,
    job: CrawlJob,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    now: datetime | None = None,
) -> CrawlJob:
    """failed_retryable 로 전이(attempt_count+1)."""
    return await transition_job(
        session, job, CrawlJobStatus.failed_retryable,
        error_code=error_code, error_message=error_message, now=now,
    )


async def mark_failed_terminal(
    session: AsyncSession,
    job: CrawlJob,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    now: datetime | None = None,
) -> CrawlJob:
    """failed_terminal 로 전이."""
    return await transition_job(
        session, job, CrawlJobStatus.failed_terminal,
        error_code=error_code, error_message=error_message, now=now,
    )


async def mark_cancelled(session: AsyncSession, job: CrawlJob, *, now: datetime | None = None) -> CrawlJob:
    """cancelled 로 전이."""
    return await transition_job(session, job, CrawlJobStatus.cancelled, now=now)