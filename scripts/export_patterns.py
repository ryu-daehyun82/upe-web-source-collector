"""적재된 web_patterns → GPT 활용용 zip 내보내기.

web_patterns 에서 추상화 패턴(feature_json)을 읽어 다음 3개로 묶는다:
  - patterns.jsonl  : 패턴 1건/1줄 (구조 feature + 메타)
  - summary.json    : 집계(레이아웃 분포·카테고리 빈도·평균 영역비율·표 통계·빈출 section_order)
  - README.md       : 스키마·사용법(GPT 프롬프트용)
원본 표현은 애초에 미포함(구조 패턴만). 비상업 개인프로젝트 가정.

사용:
  python scripts/export_patterns.py --db-url sqlite+aiosqlite:///doclaynet.db --output out.zip
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import zipfile
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.models.tables import WebPattern  # noqa: E402


async def _load_patterns(db_url: str, *, pattern_type: str | None, limit: int | None) -> list[dict]:
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    rows: list[dict] = []
    async with factory() as session:
        stmt = select(WebPattern)
        if pattern_type:
            stmt = stmt.where(WebPattern.pattern_type == pattern_type)
        result = (await session.execute(stmt)).scalars().all()
        for p in result:
            rows.append({
                "id": str(p.id),
                "pattern_type": p.pattern_type,
                "license_status": p.license_status,
                "original_reuse_risk": p.original_reuse_risk,
                "pattern_status": p.pattern_status,
                "feature": p.feature_json,
            })
            if limit and len(rows) >= limit:
                break
    await engine.dispose()
    return rows


def _build_summary(rows: list[dict]) -> dict:
    by_type = Counter(r["pattern_type"] for r in rows)
    by_status = Counter(r["pattern_status"] for r in rows)
    by_risk = Counter(r["original_reuse_risk"] for r in rows)
    by_layout = Counter((r["feature"] or {}).get("layout_type") for r in rows)

    cat_freq: Counter = Counter()           # 카테고리가 등장한 패턴 수
    cat_ratio_sum: dict[str, float] = defaultdict(float)
    cat_ratio_n: dict[str, int] = defaultdict(int)
    table_pages = 0
    table_total = 0
    seq_freq: Counter = Counter()           # section_order 시퀀스 빈도

    for r in rows:
        f = r["feature"] or {}
        so = f.get("section_order") or []
        for cat in set(so):
            cat_freq[cat] += 1
        for cat, ratio in (f.get("region_ratios") or {}).items():
            cat_ratio_sum[cat] += float(ratio)
            cat_ratio_n[cat] += 1
        tbl = (f.get("table_structure") or {}).get("tables", 0)
        if tbl:
            table_pages += 1
            table_total += tbl
        if so:
            seq_freq[tuple(so)] += 1

    avg_ratio = {c: round(cat_ratio_sum[c] / cat_ratio_n[c], 4) for c in cat_ratio_sum}
    top_sequences = [
        {"section_order": list(seq), "count": cnt}
        for seq, cnt in seq_freq.most_common(30)
    ]

    return {
        "total_patterns": len(rows),
        "by_pattern_type": dict(by_type),
        "by_status": dict(by_status),
        "by_reuse_risk": dict(by_risk),
        "by_layout_type": {str(k): v for k, v in by_layout.items()},
        "category_frequency": dict(cat_freq.most_common()),
        "avg_region_ratio_by_category": avg_ratio,
        "table_presence_rate": round(table_pages / len(rows), 4) if rows else 0.0,
        "avg_tables_when_present": round(table_total / table_pages, 2) if table_pages else 0.0,
        "top_section_order_sequences": top_sequences,
    }


_README = """# UPE 구조적 패턴 데이터 (GPT 활용용)

UPE Web Source Collector 가 거버넌스(원본 표현 제거 + Reuse Risk + G4)를 통과시켜
추출한 **문서/시각자료의 구조적 레이아웃 패턴**. 원본 텍스트·이미지 픽셀은 포함하지 않으며,
영역 유형·순서·비율 같은 추상 구조만 담는다.

## 파일
- `patterns.jsonl` — 패턴 1건/1줄. 각 줄:
  ```json
  {{"id": "...", "pattern_type": "document_layout|chart",
    "license_status": "allowed|conditional_approved",
    "original_reuse_risk": "low", "pattern_status": "approved",
    "feature": {{...구조 feature...}}}}
  ```
- `summary.json` — 집계: 레이아웃 분포, 카테고리 빈도, 카테고리별 평균 영역비율,
  표 통계, **빈출 section_order 시퀀스**(생성 시 참고용 레이아웃 템플릿).
- `README.md` — 이 문서.

## feature 스키마 (document_layout)
- `layout_type`: "document"
- `section_order`: 읽기순서(위→아래, 좌→우) 영역 유형 시퀀스.
  유형 = Title/Section-header/Text/List-item/Caption/Footnote/Formula/Table/Picture/Page-header/Page-footer
- `region_ratios`: 유형별 페이지 면적 점유 비율(0~1)
- `table_structure`: {{"tables": N, "rows": 0}}
- `card_count`: List-item 수
- (raw_text_removed/image_pixels_removed/removed_items 는 거버넌스 감사 플래그)

## feature 스키마 (chart)
- `layout_type`: "chart" / `chart_type`: bar/line/pie/...
- `category_count` / `series_count` / `color_count`
- `legend_present` / `data_labels_present`

## GPT 활용 예
- "이 레이아웃 패턴 분포를 학습해 새 문서/슬라이드 레이아웃을 제안해줘"
- `top_section_order_sequences` 를 레이아웃 템플릿으로 사용
- `avg_region_ratio_by_category` 로 영역 크기 배분 가이드

⚠️ 원본 콘텐츠가 아니라 **구조 패턴 통계**입니다. 출처 데이터셋 라이선스(예: DocLayNet
CDLA-Permissive, AIHub 비상업)를 준수하세요.
"""


async def _run(args) -> int:
    rows = await _load_patterns(args.db_url, pattern_type=args.pattern_type, limit=args.limit)
    if not rows:
        print("[export] 패턴 0건 — DB/필터 확인")
        return 1
    summary = _build_summary(rows)

    jsonl = io.StringIO()
    for r in rows:
        jsonl.write(json.dumps(r, ensure_ascii=False) + "\n")

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("patterns.jsonl", jsonl.getvalue())
        zf.writestr("summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
        zf.writestr("README.md", _README)

    size = os.path.getsize(args.output)
    print(f"[export] {len(rows)} patterns → {args.output} ({size/1024:.1f} KB)")
    print(f"[export] pattern_type={summary['by_pattern_type']} layout={summary['by_layout_type']}")
    print(f"[export] 빈출 section_order top3:")
    for s in summary["top_section_order_sequences"][:3]:
        print(f"   x{s['count']}: {s['section_order'][:8]}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="web_patterns → GPT 활용용 zip 내보내기")
    p.add_argument("--db-url", required=True, help="async DB URL (예: sqlite+aiosqlite:///x.db)")
    p.add_argument("--output", default="upe_patterns_export.zip", help="출력 zip 경로")
    p.add_argument("--pattern-type", default=None, help="필터(document_layout/chart)")
    p.add_argument("--limit", type=int, default=None, help="최대 패턴 수")
    return asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())