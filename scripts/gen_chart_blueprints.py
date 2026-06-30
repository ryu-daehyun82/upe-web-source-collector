"""차트 Blueprint 생성 (데이터 의도별 차트 추천, 실측 grounding).

블루프린트(슬라이드)처럼, "데이터 의도(intent)면 → 이 차트유형을, 이 구조로"를
큐레이션. AIHub 9,000 차트 실측 통계로 typical_category/series/color 를 grounding.
원본 값/라벨 미포함(구조 권장만). UPE chart 패턴과 동일 키 위주(chart_type/category_count/
series_count/color_count/legend_present/data_labels_present) + intent 메타.

실측(내장): bar(cat 4.85,col 2.4) line(cat 5.8,col 3) pie(cat 4.1,col 5.1) mixed(cat 4.7,col 2).
--patterns 로 chart patterns.jsonl 주면 재계산.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile
from collections import defaultdict

# AIHub 9,000 차트 유형별 실측(내장 기본). --patterns 로 덮어씀.
_CHART_TYPE_STATS: dict[str, dict] = {
    "bar":   {"n": 6418, "avg_category": 4.85, "avg_series": 1.21, "avg_color": 2.42, "legend_rate": 1.0, "data_label_rate": 1.0},
    "line":  {"n": 1246, "avg_category": 5.79, "avg_series": 1.0, "avg_color": 3.0, "legend_rate": 1.0, "data_label_rate": 1.0},
    "pie":   {"n": 570,  "avg_category": 4.11, "avg_series": 1.0, "avg_color": 5.1, "legend_rate": 1.0, "data_label_rate": 1.0},
    "mixed": {"n": 766,  "avg_category": 4.73, "avg_series": 1.0, "avg_color": 2.0, "legend_rate": 1.0, "data_label_rate": 1.0},
}

# 데이터 의도 → 추천 차트유형 + 구조 가이드(큐레이션).
_CHART_INTENTS: list[dict] = [
    {"intent": "comparison", "chart_type": "bar", "subtype": "vertical", "series": 1, "max_categories": 8,
     "genre": ["proposal", "business_plan", "strategy_report"],
     "when_to_use": "항목 간 값 비교(소수 범주)"},
    {"intent": "ranking", "chart_type": "bar", "subtype": "horizontal", "series": 1, "max_categories": 12,
     "genre": ["strategy_report", "business_plan"],
     "when_to_use": "크기 순 정렬·순위(범주명 긴 경우 가로)"},
    {"intent": "trend", "chart_type": "line", "subtype": "basic", "series": 1, "max_categories": 12,
     "genre": ["business_plan", "strategy_report", "proposal"],
     "when_to_use": "시간/순서에 따른 추세·변화"},
    {"intent": "multi_trend", "chart_type": "line", "subtype": "basic", "series": 3, "max_categories": 12,
     "genre": ["business_plan", "strategy_report"],
     "when_to_use": "여러 계열의 추세 동시 비교(과다 계열 지양)"},
    {"intent": "composition", "chart_type": "pie", "subtype": "pie", "series": 1, "max_categories": 6,
     "genre": ["company_intro", "business_plan", "proposal"],
     "when_to_use": "부분-전체 구성비(범주 6개 이하 권장)"},
    {"intent": "composition_over_time", "chart_type": "bar", "subtype": "stacked", "series": 3, "max_categories": 8,
     "genre": ["business_plan", "strategy_report"],
     "when_to_use": "시간별 구성비 변화(누적 막대)"},
    {"intent": "part_comparison", "chart_type": "bar", "subtype": "grouped", "series": 3, "max_categories": 8,
     "genre": ["proposal", "business_plan"],
     "when_to_use": "범주별 다계열 비교(그룹 막대)"},
    {"intent": "dual_metric", "chart_type": "mixed", "subtype": "bar+line", "series": 2, "max_categories": 8,
     "genre": ["business_plan", "strategy_report"],
     "when_to_use": "규모(막대)+추세(선) 동시(이중축)"},
    # 실측 데이터에 없는 유형 — 권장만(available_in_data=False)
    {"intent": "correlation", "chart_type": "scatter", "subtype": "scatter", "series": 1, "max_categories": 0,
     "genre": ["strategy_report", "business_plan"],
     "when_to_use": "두 변수 상관관계(산점도)"},
    {"intent": "distribution", "chart_type": "histogram", "subtype": "histogram", "series": 1, "max_categories": 0,
     "genre": ["strategy_report", "business_plan"],
     "when_to_use": "값 분포(히스토그램/박스플롯)"},
]


def load_stats(patterns_path: str | None) -> dict[str, dict]:
    """chart patterns.jsonl 에서 유형별 통계 재계산. 없으면 내장."""
    if not patterns_path or not os.path.exists(patterns_path):
        return dict(_CHART_TYPE_STATS)
    agg: dict[str, dict] = defaultdict(lambda: {"n": 0, "cat": 0, "ser": 0, "col": 0, "leg": 0, "dl": 0})
    with open(patterns_path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            ft = r.get("feature", r)
            t = ft.get("chart_type", "unknown")
            a = agg[t]
            a["n"] += 1
            a["cat"] += ft.get("category_count", 0)
            a["ser"] += ft.get("series_count", 0)
            a["col"] += ft.get("color_count", 0)
            a["leg"] += 1 if ft.get("legend_present") else 0
            a["dl"] += 1 if ft.get("data_labels_present") else 0
    out: dict[str, dict] = {}
    for t, a in agg.items():
        n = a["n"] or 1
        out[t] = {"n": a["n"], "avg_category": round(a["cat"] / n, 2), "avg_series": round(a["ser"] / n, 2),
                  "avg_color": round(a["col"] / n, 2), "legend_rate": round(a["leg"] / n, 3),
                  "data_label_rate": round(a["dl"] / n, 3)}
    return out


def build_chart_blueprints(stats: dict[str, dict] | None = None) -> list[dict]:
    stats = stats or _CHART_TYPE_STATS
    out: list[dict] = []
    for i, it in enumerate(_CHART_INTENTS):
        ct = it["chart_type"]
        st = stats.get(ct)
        available = st is not None and st.get("n", 0) > 0
        # 구조 기본값: 실측 평균(있으면) 반올림, series 는 intent 지정 우선.
        typical_cat = round(st["avg_category"]) if available else 0
        typical_col = round(st["avg_color"]) if available else max(1, it["series"])
        out.append({
            "blueprint_id": f"{it['intent']}_{i:02d}",
            "intent": it["intent"],
            "recommended_chart_type": ct,
            "subtype": it["subtype"],
            "genre": it["genre"],
            "layout_type": "chart",
            "when_to_use": it["when_to_use"],
            "structure": {
                "typical_category_count": typical_cat,
                "typical_series_count": it["series"],
                "typical_color_count": typical_col,
                "legend": True,
                "data_labels": True,
                "max_categories_guideline": it["max_categories"],
            },
            "empirical": st if available else None,
            "available_in_data": available,
        })
    return out


def build_summary(bps: list[dict], stats: dict[str, dict]) -> dict:
    from collections import Counter
    by_type = Counter(b["recommended_chart_type"] for b in bps)
    genre_index: dict[str, list[str]] = defaultdict(list)
    for b in bps:
        for g in b["genre"]:
            genre_index[g].append(b["blueprint_id"])
    return {
        "total_chart_blueprints": len(bps),
        "intents": [b["intent"] for b in bps],
        "by_recommended_chart_type": dict(by_type),
        "genre_index": dict(genre_index),
        "empirical_stats_by_type": stats,
        "note": "데이터 의도→차트유형 추천. typical_* 는 AIHub 9천 실측 grounding. "
                "scatter/histogram 은 실측 데이터에 없어 권장만(available_in_data=false).",
    }


_README = """# 차트 Blueprint (데이터 의도별 차트 추천)

슬라이드 블루프린트처럼, **데이터 의도(intent) → 추천 차트유형 + 구조**를 큐레이션.
AIHub 9,000 차트 실측으로 typical_category/series/color 를 grounding.

## 파일
- `chart_blueprints.jsonl` — 의도별 차트 블루프린트.
- `summary.json` — 의도 목록·유형 분포·장르 인덱스·유형별 실측통계.

## 스키마
```json
{
  "intent": "trend",                       // comparison/ranking/trend/composition/...
  "recommended_chart_type": "line",
  "subtype": "basic",
  "when_to_use": "시간/순서에 따른 추세·변화",
  "structure": {
     "typical_category_count": 6,          // 실측 grounding
     "typical_series_count": 1,
     "typical_color_count": 3,
     "legend": true, "data_labels": true,
     "max_categories_guideline": 12        // 가독성 상한
  },
  "empirical": {...실측...},
  "available_in_data": true                 // false면 실측 없음(권장만)
}
```

## 활용
- 데이터 목적(비교/추세/구성/상관…) → `intent` 검색 → `recommended_chart_type` + `structure` 적용
- `max_categories_guideline` 초과 시 차트 분할/요약 권장
- chart_blueprints + presentation_blueprints 결합: "KPI 슬라이드(blueprint) 안에 trend는 line(chart blueprint)"
"""


def main() -> int:
    p = argparse.ArgumentParser(description="데이터 의도별 차트 Blueprint 생성 → zip")
    p.add_argument("--patterns", default=None, help="chart patterns.jsonl(실측 재계산)")
    p.add_argument("--output", default="chart_blueprints.zip", help="출력 zip")
    args = p.parse_args()

    stats = load_stats(args.patterns)
    bps = build_chart_blueprints(stats)
    summary = build_summary(bps, stats)

    jsonl = io.StringIO()
    for b in bps:
        jsonl.write(json.dumps(b, ensure_ascii=False) + "\n")
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("chart_blueprints.jsonl", jsonl.getvalue())
        zf.writestr("summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
        zf.writestr("README.md", _README)

    print(f"[gen] {len(bps)} chart blueprints → {args.output}")
    print(f"[gen] intents={[b['intent'] for b in bps]}")
    print(f"[gen] stats source={'patterns.jsonl' if args.patterns else '내장 실측'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())