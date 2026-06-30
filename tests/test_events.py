import asyncio
import json
import uuid

from app.events import (
    make_event, ALL_TOPICS,
    InMemoryEventPublisher, NullEventPublisher, KafkaEventPublisher,
    TOPIC_CRAWL_STARTED, TOPIC_CRAWL_COMPLETED, TOPIC_PATTERN_APPROVED,
)


def test_make_event_basic():
    e = make_event(TOPIC_CRAWL_STARTED, source_id=uuid.uuid4(), job_id="j1", status="running")
    assert e["event_type"] == TOPIC_CRAWL_STARTED
    assert isinstance(e["source_id"], str)
    assert e["job_id"] == "j1"
    assert e["status"] == "running"
    assert e["payload"] == {}
    assert isinstance(e["event_id"], str)
    assert isinstance(e["created_at"], str)


def test_make_event_explicit_id_and_payload():
    e = make_event(TOPIC_CRAWL_STARTED, event_id="E1", payload={"k": 1})
    assert e["event_id"] == "E1"
    assert e["payload"] == {"k": 1}


def test_make_event_none_ids():
    e = make_event(TOPIC_CRAWL_STARTED)
    assert e["source_id"] is None and e["job_id"] is None and e["pattern_id"] is None


def test_all_topics_count():
    assert len(ALL_TOPICS) == 14
    assert TOPIC_CRAWL_STARTED in ALL_TOPICS
    assert TOPIC_PATTERN_APPROVED in ALL_TOPICS


def test_inmemory_publisher():
    pub = InMemoryEventPublisher()
    asyncio.run(pub.publish("t1", make_event(TOPIC_CRAWL_STARTED)))
    asyncio.run(pub.publish("t2", make_event(TOPIC_CRAWL_COMPLETED)))
    assert len(pub.events) == 2
    assert pub.topics() == ["t1", "t2"]
    assert len(pub.by_type(TOPIC_CRAWL_STARTED)) == 1
    assert len(pub.by_type(TOPIC_CRAWL_COMPLETED)) == 1


def test_null_publisher():
    pub = NullEventPublisher()
    assert asyncio.run(pub.publish("t", make_event(TOPIC_CRAWL_STARTED))) is None


def test_kafka_publisher_serializes():
    class _FakeProducer:
        def __init__(self):
            self.sent = []

        async def send_and_wait(self, topic, value):
            self.sent.append((topic, value))

    prod = _FakeProducer()
    pub = KafkaEventPublisher(prod)
    ev = make_event(TOPIC_CRAWL_STARTED, source_id="s1", status="running")
    asyncio.run(pub.publish("upe.web.crawl.started", ev))
    assert len(prod.sent) == 1
    topic, value = prod.sent[0]
    assert topic == "upe.web.crawl.started"
    assert isinstance(value, (bytes, bytearray))
    restored = json.loads(value)
    assert restored["event_type"] == TOPIC_CRAWL_STARTED and restored["source_id"] == "s1"