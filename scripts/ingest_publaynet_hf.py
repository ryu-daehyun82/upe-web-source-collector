"""PubLayNet(HuggingFace parquet 미러) → web_patterns 적재.

PubLayNet 공식 CDN(dax-cdn) 대신 HF parquet 미러(예: vikp/publaynet_bench)에서
페이지별 bbox(xyxy)+label 을 읽어 parse_doclaynet_page(COCO xywh)로 변환 → 거버넌스 →
영속화. PubLayNet 도 CDLA-Permissive → license_status=allowed. 카테고리(Text/Title/List/
Table/Figure)는 parser 가 그대로 사용(이름 기반).

deps(운영/적재 전용): huggingface_hub · pyarrow · Pillow.
사용:
  python scripts/ingest_publaynet_hf.py --repo vikp/publaynet_bench \
    --file data/train-00000-of-00001.parquet --db-url sqlite+aiosqlite:///patterns.db --create-tables
"""
from __future__ import annotations

import argparse
import asyncio
import io
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.models.tables import Base, WebSource, WebPattern  # noqa: E402
from app.models.enums import SourceStatus  # noqa: E402
from app.workers.doclaynet_parser import parse_doclaynet_page  # noqa: E402
from app.pattern_build import build_and_persist_pattern  # noqa: E402


def row_to_regions(bboxes, labels) -> list[dict]:
    """(bboxes, labels) → regions[{category,bbox[x,y,w,h]}]. bbox xyxy 면 wh 로 변환.

    x2>x1 & y2>y1 이면 xyxy 로 보고 w=x2-x1,h=y2-y1. 아니면 이미 xywh 로 간주.
    길이<4 bbox 는 스킵.
    """
    regions: list[dict] = []
    for bb, lab in zip(bboxes, labels):
        if bb is None or len(bb) < 4:
            continue
        x1, y1, x2, y2 = bb[0], bb[1], bb[2], bb[3]
        if x2 > x1 and y2 > y1:
            w, h = x2 - x1, y2 - y1
        else:
            w, h = x2, y2
        regions.append({"category": str(lab), "bbox": [x1, y1, w, h]})
    return regions


def _iter_pages(parquet_path: str):
    import pyarrow.parquet as pq
    from PIL import Image

    t = pq.read_table(parquet_path, columns=["image", "bboxes", "labels"]).to_pydict()
    for img, bbs, labs in zip(t["image"], t["bboxes"], t["labels"]):
        w, h = Image.open(io.BytesIO(img["bytes"])).size
        yield w, h, row_to_regions(bbs, labs)


async def _run(args) -> int:
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(repo_id=args.repo, filename=args.file, repo_type="dataset")
    print(f"[publaynet] parquet={path} ({os.path.getsize(path) // 1048576} MB)")

    engine = create_async_engine(args.db_url)
    if args.create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    async with factory() as session:
        sid = uuid.uuid4()
        session.add(WebSource(
            id=sid, url=f"https://huggingface.co/datasets/{args.repo}", domain="huggingface.co",
            source_type="publaynet", crawl_status=SourceStatus.parsed.value,
            license_status="allowed", discovery_method="hf_parquet",
        ))
        await session.flush()
        n = appr = skip = 0
        for w, h, regions in _iter_pages(path):
            feat = parse_doclaynet_page(w, h, regions)
            if feat.get("layout_type") == "unknown":
                skip += 1
                continue
            _p, d = await build_and_persist_pattern(
                session, source_id=sid, raw_feature=feat,
                pattern_type="document_layout", license_status="allowed",
            )
            n += 1
            appr += 1 if d.operational else 0
            if args.limit and n >= args.limit:
                break
        await session.commit()
        total = len((await session.execute(select(WebPattern))).scalars().all())
    await engine.dispose()
    print(f"[publaynet] persisted={n} approved={appr} skipped_unknown={skip}")
    print(f"[db] web_patterns total={total}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="PubLayNet(HF parquet) → web_patterns 적재")
    p.add_argument("--repo", default="vikp/publaynet_bench", help="HF dataset repo")
    p.add_argument("--file", default="data/train-00000-of-00001.parquet", help="parquet 경로")
    p.add_argument("--db-url", default=None, help="async DB URL (기본 UPE_DATABASE_URL 또는 sqlite 파일)")
    p.add_argument("--create-tables", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    if not args.db_url:
        args.db_url = os.getenv("UPE_DATABASE_URL", "sqlite+aiosqlite:///upe_patterns.db")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())