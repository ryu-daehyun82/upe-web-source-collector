"""재시도 정책 스케줄러 (설계 §7.3).

에러 종류별 최대 재시도·백오프를 강제하고, 재큐(backoff) 또는 terminal 전이를 영속화.
runner 는 즉시 분류(failed_retryable/terminal)만 하고, 본 모듈이 §7.3 max_retries 를
강제해 재큐(running→failed_retryable→queued + scheduled_at) 또는 영구 실패(failed_terminal)를 결정한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import CrawlJobStatus
from app.models.tables import CrawlJob
from app.job_state import mark_failed_retryable, mark_failed_terminal, transition_job


# §7.3 재시도 정책
RETRY_POLICY: dict[str, dict] = {
    "network_timeout":   {"max_retries": 3, "backoff_base_ms": 1000, "backoff_factor": 2, "terminal_reason": "network_exhausted"},
    "rate_limited":      {"max_retries": 5, "backoff_base_ms": 5000, "backoff_factor": 2, "terminal_reason": "rate_limit_exhausted"},
    "parser_error":      {"max_retries": 2, "backoff_base_ms": 1000, "backoff_factor": 2, "terminal_reason": "parser_exhausted"},
    "forbidden":         {"max_retries": 0, "backoff_base_ms": 0, "backoff_factor": 1, "terminal_reason": "forbidden"},
    "robots_disallow":   {"max_retries": 0, "backoff_base_ms": 0, "backoff_factor": 1, "terminal_reason": "blocked_by_robots"},
    "content_too_large": {"max_retries": 0, "backoff_base_ms": 0, "backoff_factor": 1, "terminal_reason": "content_too_large"},
    "pii_detected":      {"max_retries": 0, "backoff_base_ms": 0, "backoff_factor": 1, "terminal_reason": "blocked_sensitive"},
}

DEFAULT_POLICY: dict = {"max_retries": 0, "backoff_base_ms": 0, "backoff_factor": 1, "terminal_reason": "unknown_error"}

# runner 실패 사유 → §7.3 에러 코드 매핑(호출부 편의)
REASON_TO_ERROR_CODE: dict[str, str] = {
    "fetch_failed": "network_timeout",
    "render_failed": "network_timeout",
    "too_large": "content_too_large",
    "content_type_blocked": "forbidden",
    "encrypted_pdf": "forbidden",
}

MAX_DELAY_MS: int = 300000  # 5분 상한


@dataclass
class RetryDecision:
    """재시도 결정 결과."""
    should_retry: bool
    delay_ms: int
    next_attempt: int
    terminal_reason: str | None


def decide_retry(error_code: str, attempt_count: int, *, max_delay_ms: int = MAX_DELAY_MS) -> RetryDecision:
    """§7.3 정책으로 재시도 여부·백오프 결정(순수).

    attempt_count >= max_retries 면 terminal. 아니면 exponential backoff(상한 max_delay_ms).
    """
    policy = RETRY_POLICY.get(error_code, DEFAULT_POLICY)
    if attempt_count >= policy["max_retries"]:
        return RetryDecision(False, 0, attempt_count, policy["terminal_reason"])

    delay_ms = int(policy["backoff_base_ms"] * (policy["backoff_factor"] ** attempt_count))
    delay_ms = min(delay_ms, max_delay_ms)
    return RetryDecision(True, delay_ms, attempt_count + 1, None)


async def schedule_retry_or_fail(
    session: AsyncSession,
    job: CrawlJob,
    *,
    error_code: str,
    error_message: str | None = None,
    now: datetime | None = None,
) -> dict:
    """running job 의 실패를 §7.3 정책으로 처리(상태 영속화).

    재시도 가능 → failed_retryable(attempt+1) → queued + scheduled_at(now+backoff).
    소진 → failed_terminal. job 은 running 상태 전제.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    decision = decide_retry(error_code, job.attempt_count)

    if decision.should_retry:
        await mark_failed_retryable(session, job, error_code=error_code, error_message=error_message, now=now)
        await transition_job(session, job, CrawlJobStatus.queued, now=now)
        job.scheduled_at = now + timedelta(milliseconds=decision.delay_ms)
        await session.flush()
        return {
            "action": "retry_scheduled",
            "attempt": job.attempt_count,
            "delay_ms": decision.delay_ms,
            "scheduled_at": job.scheduled_at,
        }

    await mark_failed_terminal(session, job, error_code=error_code, error_message=error_message, now=now)
    return {
        "action": "terminal",
        "attempt": job.attempt_count,
        "terminal_reason": decision.terminal_reason,
    }