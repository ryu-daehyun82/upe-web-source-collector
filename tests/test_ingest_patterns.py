import asyncio
import json
import os
import sys

import pytest

pytest.importorskip("aiosqlite")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.tables import Base, WebSource, WebPattern
from ingest_patterns import (  # noqa: E402  (scripts/ 경로 추가 후)
    load_features, load_features_from_input, ingest_features, SOURCE_CONFIG,
)


@pytest.fixture()
def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    asyncio.run(engine.dispose())


_COCO = {
    "images": [
        {"id": 1, "width": 1000, "height": 1000},
        {"id": 2, "width": 1000, "height": 1000},  # 어노테이션 없음 → unknown
    ],
    "annotations": [
        {"image_id": 1, "category_id": 11, "bbox": [0, 0, 1000, 100]},
        {"image_id": 1, "category_id": 10, "bbox": [0, 200, 1000, 600]},
    ],
    "categories": [{"id": 11, "name": "Title"}, {"id": 10, "name": "Text"}],
}

_CHARTS = [
    {"type": "bar", "categories": [1, 2, 3], "colors": ["a", "b"], "legend": ["x"]},
    {"type": "pie", "categories": [1, 2]},
]


def test_source_config_keys():
    assert "doclaynet" in SOURCE_CONFIG and "chart" in SOURCE_CONFIG
    assert SOURCE_CONFIG["doclaynet"]["license_status"] == "allowed"
    assert SOURCE_CONFIG["chart"]["license_status"] == "conditional_approved"


def test_load_features_doclaynet(tmp_path):
    p = tmp_path / "coco.json"
    p.write_text(json.dumps(_COCO), encoding="utf-8")
    feats = load_features("doclaynet", str(p))
    assert len(feats) == 2
    assert feats[0]["section_order"] == ["Title", "Text"]
    assert feats[1]["layout_type"] == "unknown"


def test_load_features_chart_list_and_dir(tmp_path):
    p = tmp_path / "charts.json"
    p.write_text(json.dumps(_CHARTS), encoding="utf-8")
    feats = load_features("chart", str(p))
    assert len(feats) == 2
    assert feats[0]["chart_type"] == "bar"
    # 디렉터리 입력 glob
    feats_dir = load_features_from_input("chart", str(tmp_path))
    assert len(feats_dir) == 2


def test_ingest_doclaynet_persists_and_skips_unknown(session_factory):
    async def _t():
        async with session_factory() as session:
            feats = load_features("doclaynet", _write(session_factory, _COCO))
            summary = await ingest_features(
                session, source="doclaynet", features=feats,
                source_url="https://github.com/DS4SD/DocLayNet",
                pattern_type="document_layout", license_status="allowed",
            )
            await session.commit()
            # 페이지1 적재, 페이지2(unknown) 스킵
            assert summary["total"] == 1
            assert summary["approved"] == 1
            assert summary["skipped_unknown"] == 1
            rows = (await session.execute(select(WebPattern))).scalars().all()
            assert len(rows) == 1
            assert rows[0].pattern_type == "document_layout"
            assert rows[0].pattern_status == "approved"
            # WebSource 데이터셋 행 생성
            src = (await session.execute(select(WebSource))).scalar_one()
            assert src.source_type == "doclaynet"
            assert src.crawl_status == "parsed"
            assert src.license_status == "allowed"
    asyncio.run(_t())


def test_ingest_chart_conditional(session_factory):
    async def _t():
        async with session_factory() as session:
            feats = parse_chart_features()
            summary = await ingest_features(
                session, source="chart", features=feats,
                source_url=SOURCE_CONFIG["chart"]["default_url"],
                pattern_type="chart", license_status="conditional_approved",
            )
            await session.commit()
            assert summary["total"] == 2
            rows = (await session.execute(select(WebPattern))).scalars().all()
            assert len(rows) == 2
            assert all(r.license_status == "conditional_approved" for r in rows)
            assert all(r.pattern_type == "chart" for r in rows)
    asyncio.run(_t())


def test_ingest_limit(session_factory):
    async def _t():
        async with session_factory() as session:
            feats = parse_chart_features()
            summary = await ingest_features(
                session, source="chart", features=feats,
                source_url="https://x", pattern_type="chart",
                license_status="conditional_approved", limit=1,
            )
            await session.commit()
            assert summary["total"] == 1
    asyncio.run(_t())


# ── helpers ──────────────────────────────────────────────────────

def _write(_factory, data):
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


def parse_chart_features():
    from app.workers.chart_pattern_parser import parse_chart_dataset
    return parse_chart_dataset(_CHARTS)