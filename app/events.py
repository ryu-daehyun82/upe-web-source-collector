"""이벤트 발행 (§7 Queue/Event).

§7.1 토픽 상수 + §7.2 공통 스키마 빌더 + 발행자(어댑터+폴백).
발행자: InMemoryEventPublisher(테스트/로컬) · NullEventPublisher(비활성) ·
KafkaEventPublisher(실 Kafka, 주입형 producer). aiokafka 는 create_kafka_producer 내부 지역 import.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

# §7.1 토픽 상수
TOPIC_SOURCE_DISCOVERED = "upe.web.source.discovered"
TOPIC_POLICY_CHECK_REQUESTED = "upe.web.policy.check.requested"
TOPIC_POLICY_CHECKED = "upe.web.policy.checked"
TOPIC_CRAWL_REQUESTED = "upe.web.crawl.requested"
TOPIC_CRAWL_STARTED = "upe.web.crawl.started"
TOPIC_CRAWL_COMPLETED = "upe.web.crawl.completed"
TOPIC_CRAWL_FAILED = "upe.web.crawl.failed"
TOPIC_PARSE_REQUESTED = "upe.web.parse.requested"
TOPIC_PARSE_COMPLETED = "upe.web.parse.completed"
TOPIC_PATTERN_BUILT = "upe.web.pattern.built"
TOPIC_PATTERN_APPROVED = "upe.web.pattern.approved"
TOPIC_PATTERN_BLOCKED = "upe.web.pattern.blocked"
TOPIC_DELETE_REQUESTED = "upe.web.delete.requested"
TOPIC_DELETE_COMPLETED = "upe.web.delete.completed"

ALL_TOPICS: frozenset[str] = frozenset({
    TOPIC_SOURCE_DISCOVERED, TOPIC_POLICY_CHECK_REQUESTED, TOPIC_POLICY_CHECKED,
    TOPIC_CRAWL_REQUESTED, TOPIC_CRAWL_STARTED, TOPIC_CRAWL_COMPLETED, TOPIC_CRAWL_FAILED,
    TOPIC_PARSE_REQUESTED, TOPIC_PARSE_COMPLETED, TOPIC_PATTERN_BUILT,
    TOPIC_PATTERN_APPROVED, TOPIC_PATTERN_BLOCKED, TOPIC_DELETE_REQUESTED, TOPIC_DELETE_COMPLETED,
})


def make_event(
    event_type: str,
    *,
    source_id=None,
    job_id=None,
    pattern_id=None,
    trace_id: str | None = None,
    status: str | None = None,
    payload: dict[str, Any] | None = None,
    event_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """§7.2 공통 이벤트 스키마 dict. id 들은 str 화, created_at 은 ISO8601.

    {event_id, event_type, source_id, job_id, pattern_id, trace_id, status, payload, created_at}.
    event_id/created_at 미지정 시 각각 uuid4/now 생성. payload None→{}.
    """
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "source_id": str(source_id) if source_id is not None else None,
        "job_id": str(job_id) if job_id is not None else None,
        "pattern_id": str(pattern_id) if pattern_id is not None else None,
        "trace_id": trace_id,
        "status": status,
        "payload": payload if payload is not None else {},
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }


class InMemoryEventPublisher:
    """테스트/로컬용. 발행 이벤트를 메모리에 기록."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []   # (topic, event)

    async def publish(self, topic: str, event: dict) -> None:
        self.events.append((topic, event))

    def by_type(self, event_type: str) -> list[dict]:
        """event_type 이 일치하는 이벤트 리스트."""
        return [e for t, e in self.events if e.get("event_type") == event_type]

    def topics(self) -> list[str]:
        """발행된 topic 리스트(순서대로)."""
        return [t for t, e in self.events]


class NullEventPublisher:
    """이벤트 비활성(no-op)."""

    async def publish(self, topic: str, event: dict) -> None:
        return None


class KafkaEventPublisher:
    """실 Kafka 발행. 주입형 producer(덕타이핑: async send_and_wait(topic, value: bytes)).

    producer 생성/시작/정지는 호출부 책임. 본 클래스는 직렬화+전송만.
    """

    def __init__(self, producer) -> None:
        self.producer = producer

    async def publish(self, topic: str, event: dict) -> None:
        """이벤트를 JSON 직렬화 후 Kafka 로 전송."""
        value = json.dumps(event, ensure_ascii=False).encode("utf-8")
        await self.producer.send_and_wait(topic, value)


async def create_kafka_producer(bootstrap_servers: str):
    """aiokafka AIOKafkaProducer 생성·시작(운영용). 지역 import. 호출부가 stop 책임."""
    from aiokafka import AIOKafkaProducer

    producer = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
    await producer.start()
    return producer