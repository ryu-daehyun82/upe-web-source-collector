"""패턴 배치 적재 스크립트.

데이터셋 JSON(DocLayNet COCO / AIHub 차트 메타) → 파서 → 거버넌스 → web_patterns 저장.
하나의 WebSource(데이터셋 단위)에 페이지/차트별 패턴을 붙인다. 거버넌스 결과(approved/
blocked)는 그대로 영속화하고 요약을 출력한다.

사용:
  python scripts/ingest_patterns.py --source doclaynet --input coco.json --create-tables
  python scripts/ingest_patterns.py --source chart --input charts/ --db-url postgresql+asyncpg://localhost/upe

원본 표현(원문/픽셀)은 파서 단계에서 이미 제거됨 → 패턴만 저장. 라이선스는 source 별 기본값
(doclaynet=allowed / chart=conditional_approved, 비상업 개인프로젝트)로 태깅.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import sys
import uuid

# cwd 무관하게 repo 루트를 import 경로에 추가.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.models.tables import Base, WebSource, WebPattern  # noqa: E402
from app.models.enums import SourceStatus  # noqa: E402
from app.pattern_build import build_and_persist_pattern  # noqa: E402
from app.policy.url_canon import extract_domain  # noqa: E402
from app.workers.doclaynet_parser import parse_doclaynet_coco  # noqa: E402
from app.workers.chart_pattern_parser import parse_chart_dataset  # noqa: E402
from app.workers.aihub_chart_parser import parse_aihub_chart  # noqa: E402


# source 별 설정(패턴 타입·라이선스 태그)
SOURCE_CONFIG: dict[str, dict] = {
    "doclaynet": {
        "pattern_type": "document_layout",
        "license_status": "allowed",           # CDLA-Permissive-1.0
        "default_url": "https://github.com/DS4SD/DocLayNet",
    },
    "chart": {
        "pattern_type": "chart",
        "license_status": "conditional_approved",  # AIHub 비상업·약관 수락
        "default_url": "https://www.aihub.or.kr/aihubdata/data/view.do?dataSetSn=71957",
    },
    "aihub_chart": {
        "pattern_type": "chart",
        "license_status": "conditional_approved",  # AIHub 비상업·약관 수락(제1유형)
        "default_url": "https://www.aihub.or.kr/aihubdata/data/view.do?dataSetSn=71957",
    },
}


def load_features(source: str, path: str, *, source_url: str | None = None) -> list[dict]:
    """단일 JSON 파일 → raw_feature 리스트(파서 적용)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if source == "doclaynet":
        # COCO dict(images/annotations/categories)
        return parse_doclaynet_coco(data, url=source_url or path)

    if source == "chart":
        # list[메타] 또는 {"items":[...]} 또는 단일 메타 dict
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict) and isinstance(data.get("items"), list):
            items = data["items"]
        else:
            items = [data]
        return parse_chart_dataset(items, url=source_url or path)

    if source == "aihub_chart":
        # AIHub 차트 라벨 = 파일 1개당 차트 1개(nested annotations 스키마)
        return [parse_aihub_chart(data, url=source_url or path)]

    raise ValueError(f"unknown source: {source}")


def load_features_from_input(source: str, input_path: str, *, source_url: str | None = None) -> list[dict]:
    """파일 또는 디렉터리(.json glob) → 통합 raw_feature 리스트."""
    if os.path.isdir(input_path):
        paths = sorted(glob.glob(os.path.join(input_path, "*.json")))
        if not paths:  # 중첩 디렉터리면 재귀 글롭
            paths = sorted(glob.glob(os.path.join(input_path, "**", "*.json"), recursive=True))
    else:
        paths = [input_path]
    features: list[dict] = []
    for p in paths:
        features.extend(load_features(source, p, source_url=source_url))
    return features


async def ingest_features(
    session,
    *,
    source: str,
    features: list[dict],
    source_url: str,
    pattern_type: str,
    license_status: str,
    limit: int | None = None,
) -> dict:
    """features 를 web_patterns 로 적재(데이터셋 단위 WebSource 1행에 부착). 요약 반환."""
    if limit is not None:
        features = features[:limit]

    domain = extract_domain(source_url) or f"{source}.dataset"
    sid = uuid.uuid4()
    session.add(WebSource(
        id=sid, url=source_url, domain=domain, source_type=source,
        crawl_status=SourceStatus.parsed.value, license_status=license_status,
        discovery_method="dataset_ingest",
    ))
    await session.flush()

    summary = {"source_id": str(sid), "total": 0, "approved": 0, "blocked": 0, "skipped_unknown": 0}
    for feat in features:
        # layout_type "unknown"(빈 페이지/메타)은 패턴 가치 없어 스킵.
        if feat.get("layout_type") == "unknown":
            summary["skipped_unknown"] += 1
            continue
        _pat, decision = await build_and_persist_pattern(
            session, source_id=sid, raw_feature=feat,
            pattern_type=pattern_type, license_status=license_status,
        )
        summary["total"] += 1
        summary["approved" if decision.operational else "blocked"] += 1

    return summary


async def _run(args) -> int:
    cfg = SOURCE_CONFIG[args.source]
    source_url = args.source_url or cfg["default_url"]
    license_status = args.license_status or cfg["license_status"]

    features = load_features_from_input(args.source, args.input, source_url=source_url)
    print(f"[parse] {args.source}: {len(features)} features from {args.input}")

    if args.dry_run:
        usable = sum(1 for f in features if f.get("layout_type") != "unknown")
        print(f"[dry-run] usable={usable} skipped_unknown={len(features) - usable} (DB 미기록)")
        return 0

    db_url = args.db_url or os.getenv("UPE_DATABASE_URL", "sqlite+aiosqlite:///upe_patterns.db")
    engine = create_async_engine(db_url)
    if args.create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    async with factory() as session:
        summary = await ingest_features(
            session, source=args.source, features=features, source_url=source_url,
            pattern_type=cfg["pattern_type"], license_status=license_status, limit=args.limit,
        )
        await session.commit()
        total_rows = len((await session.execute(select(WebPattern))).scalars().all())

    await engine.dispose()
    print(f"[ingest] source_id={summary['source_id']}")
    print(f"[ingest] persisted={summary['total']} approved={summary['approved']} "
          f"blocked={summary['blocked']} skipped_unknown={summary['skipped_unknown']}")
    print(f"[db] web_patterns total rows={total_rows} ({db_url})")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="UPE 패턴 배치 적재 (DocLayNet/차트 → web_patterns)")
    p.add_argument("--source", required=True, choices=sorted(SOURCE_CONFIG), help="데이터 출처")
    p.add_argument("--input", required=True, help="JSON 파일 또는 디렉터리")
    p.add_argument("--db-url", default=None, help="async DB URL (기본 UPE_DATABASE_URL 또는 sqlite 파일)")
    p.add_argument("--source-url", default=None, help="WebSource URL(미지정 시 source 기본값)")
    p.add_argument("--license-status", default=None, help="라이선스 태그 override")
    p.add_argument("--limit", type=int, default=None, help="최대 적재 개수")
    p.add_argument("--create-tables", action="store_true", help="시작 시 create_all(개발/sqlite용)")
    p.add_argument("--dry-run", action="store_true", help="파싱만, DB 미기록")
    return asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())