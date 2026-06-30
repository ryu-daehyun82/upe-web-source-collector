"""프레젠테이션 Blueprint 패턴 데이터셋 생성 (Layout Pattern Engine 학습/추천용).

DocLayNet(범용 문서 구조)의 한계를 보완 — 너희 장르(제안서/사업계획서/회사소개서/전략보고서)에
맞는 **의도(intent)별 슬라이드 Blueprint**를 큐레이션한다. 카톡 분석 인사이트 반영:
  - 좋은 문서 = Visual ~75% / Text ~25% (현재 엔진 Text 70% 문제 교정)
  - 디자인(컬러/폰트)이 아니라 **구조(배치순서/영역비율) 우선**

출력: presentation_blueprints.jsonl + summary.json(의도 인덱스·평균 visual비율·영역빈도) + README.
region_ratios 는 raw weights 를 합 1.0 으로 정규화. UPE 패턴 스키마와 호환(layout_type/
section_order/region_ratios) + intent/genre/text_visual_ratio 메타(추천 엔진 키).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile
from collections import Counter, defaultdict

# 영역 유형(프레젠테이션 지향). 시각/텍스트 분류는 _VISUAL_REGIONS.
_VISUAL_REGIONS = {"Visual", "Diagram", "Chart", "Image", "Timeline", "Flow", "PhotoGrid", "BeforeAfter"}

# 큐레이션 Blueprint — weights 는 영역 면적 가중치(정규화 전), section_order 는 읽기 순서.
_BLUEPRINTS: list[dict] = [
    {
        "intent": "cover", "genre": ["proposal", "business_plan", "company_intro", "strategy_report"],
        "section_order": ["Logo", "Title", "Subtitle", "Visual", "Footer"],
        "weights": {"Logo": 0.05, "Title": 0.18, "Subtitle": 0.1, "Visual": 0.6, "Footer": 0.07},
        "text_visual": {"text": 0.35, "visual": 0.65},
        "when_to_use": "표지/첫 장 — 큰 제목 + 배경 비주얼로 톤 설정",
    },
    {
        "intent": "agenda", "genre": ["proposal", "strategy_report"],
        "section_order": ["Title", "BulletList", "Footer"],
        "weights": {"Title": 0.15, "BulletList": 0.78, "Footer": 0.07},
        "text_visual": {"text": 0.85, "visual": 0.15},
        "when_to_use": "목차/아젠다 — 예외적으로 텍스트 위주(번호+항목)",
    },
    {
        "intent": "executive_summary", "genre": ["proposal", "business_plan", "strategy_report"],
        "section_order": ["Title", "KeyMessage", "Visual", "Metric", "Callout", "Footer"],
        "weights": {"Title": 0.1, "KeyMessage": 0.13, "Visual": 0.42, "Metric": 0.15, "Callout": 0.13, "Footer": 0.07},
        "text_visual": {"text": 0.35, "visual": 0.65},
        "when_to_use": "핵심 요약 — 한 줄 메시지 + 비주얼 + 핵심 수치",
    },
    {
        "intent": "problem", "genre": ["proposal", "business_plan", "strategy_report"],
        "section_order": ["Title", "KeyMessage", "Visual", "Body"],
        "weights": {"Title": 0.1, "KeyMessage": 0.15, "Visual": 0.5, "Body": 0.25},
        "text_visual": {"text": 0.4, "visual": 0.6},
        "when_to_use": "문제 정의 — 페인포인트 다이어그램 중심",
    },
    {
        "intent": "solution", "genre": ["proposal", "business_plan"],
        "section_order": ["Title", "Visual", "Callout", "Body"],
        "weights": {"Title": 0.1, "Visual": 0.62, "Callout": 0.15, "Body": 0.13},
        "text_visual": {"text": 0.3, "visual": 0.7},
        "when_to_use": "솔루션 — 큰 솔루션 다이어그램 + 강조 콜아웃",
    },
    {
        "intent": "architecture", "genre": ["proposal", "strategy_report"],
        "section_order": ["Title", "Diagram", "Caption", "Callout"],
        "weights": {"Title": 0.09, "Diagram": 0.7, "Caption": 0.1, "Callout": 0.11},
        "text_visual": {"text": 0.25, "visual": 0.75},
        "when_to_use": "아키텍처/구성도 — 다이어그램이 주인공",
    },
    {
        "intent": "roadmap", "genre": ["proposal", "business_plan", "strategy_report"],
        "section_order": ["Title", "Timeline", "Callout", "Caption"],
        "weights": {"Title": 0.1, "Timeline": 0.62, "Callout": 0.18, "Caption": 0.1},
        "text_visual": {"text": 0.3, "visual": 0.7},
        "when_to_use": "로드맵 — 단계 타임라인 + 마일스톤 콜아웃",
    },
    {
        "intent": "process_flow", "genre": ["proposal", "company_intro"],
        "section_order": ["Title", "Flow", "Caption"],
        "weights": {"Title": 0.1, "Flow": 0.72, "Caption": 0.18},
        "text_visual": {"text": 0.28, "visual": 0.72},
        "when_to_use": "프로세스/플로우 — 단계 흐름도",
    },
    {
        "intent": "comparison", "genre": ["proposal", "strategy_report"],
        "section_order": ["Title", "Visual", "Table", "Callout"],
        "weights": {"Title": 0.1, "Visual": 0.4, "Table": 0.35, "Callout": 0.15},
        "text_visual": {"text": 0.45, "visual": 0.55},
        "when_to_use": "비교 — 좌우 2단 비주얼/표 + 결론 콜아웃",
    },
    {
        "intent": "kpi_dashboard", "genre": ["business_plan", "strategy_report"],
        "section_order": ["Title", "Metric", "Metric", "Chart", "Caption"],
        "weights": {"Title": 0.1, "Metric": 0.3, "Chart": 0.5, "Caption": 0.1},
        "text_visual": {"text": 0.35, "visual": 0.65},
        "when_to_use": "KPI 대시보드 — 핵심 수치 + 차트",
    },
    {
        "intent": "market_analysis", "genre": ["business_plan", "strategy_report"],
        "section_order": ["Title", "Chart", "Body", "Metric"],
        "weights": {"Title": 0.1, "Chart": 0.5, "Body": 0.22, "Metric": 0.18},
        "text_visual": {"text": 0.4, "visual": 0.6},
        "when_to_use": "시장 분석 — 시장 차트 + 해석 + 수치",
    },
    {
        "intent": "business_model", "genre": ["business_plan"],
        "section_order": ["Title", "Diagram", "Callout"],
        "weights": {"Title": 0.1, "Diagram": 0.72, "Callout": 0.18},
        "text_visual": {"text": 0.28, "visual": 0.72},
        "when_to_use": "비즈니스 모델 — BMC/수익구조 다이어그램",
    },
    {
        "intent": "financials", "genre": ["business_plan", "proposal"],
        "section_order": ["Title", "Table", "Chart", "Metric"],
        "weights": {"Title": 0.1, "Table": 0.4, "Chart": 0.35, "Metric": 0.15},
        "text_visual": {"text": 0.5, "visual": 0.5},
        "when_to_use": "재무/예산 — 표 + 추이 차트 (수치 밀도 높음)",
    },
    {
        "intent": "org_team", "genre": ["company_intro", "proposal"],
        "section_order": ["Title", "PhotoGrid", "Caption", "Body"],
        "weights": {"Title": 0.1, "PhotoGrid": 0.55, "Caption": 0.2, "Body": 0.15},
        "text_visual": {"text": 0.35, "visual": 0.65},
        "when_to_use": "조직/팀 — 인물 그리드 + 역할 캡션",
    },
    {
        "intent": "case_study", "genre": ["company_intro", "proposal"],
        "section_order": ["Title", "BeforeAfter", "Metric", "Quote"],
        "weights": {"Title": 0.1, "BeforeAfter": 0.5, "Metric": 0.22, "Quote": 0.18},
        "text_visual": {"text": 0.35, "visual": 0.65},
        "when_to_use": "사례/실적 — 전후 비교 + 성과 수치 + 고객 인용",
    },
    {
        "intent": "company_overview", "genre": ["company_intro"],
        "section_order": ["Title", "Metric", "Visual", "Body"],
        "weights": {"Title": 0.1, "Metric": 0.25, "Visual": 0.45, "Body": 0.2},
        "text_visual": {"text": 0.4, "visual": 0.6},
        "when_to_use": "회사 개요 — 대표 수치 + 비주얼 + 소개",
    },
    {
        "intent": "value_proposition", "genre": ["proposal", "company_intro", "business_plan"],
        "section_order": ["Title", "KeyMessage", "Visual", "Callout"],
        "weights": {"Title": 0.1, "KeyMessage": 0.2, "Visual": 0.55, "Callout": 0.15},
        "text_visual": {"text": 0.35, "visual": 0.65},
        "when_to_use": "가치 제안 — 큰 한 줄 + 비주얼",
    },
    {
        "intent": "risk_mitigation", "genre": ["proposal", "strategy_report"],
        "section_order": ["Title", "Table", "Callout", "Body"],
        "weights": {"Title": 0.1, "Table": 0.45, "Callout": 0.2, "Body": 0.25},
        "text_visual": {"text": 0.6, "visual": 0.4},
        "when_to_use": "리스크/대응 — 표 위주(예외적 텍스트 밀도)",
    },
    {
        "intent": "closing_cta", "genre": ["proposal", "business_plan", "company_intro", "strategy_report"],
        "section_order": ["Title", "KeyMessage", "Visual", "Footer"],
        "weights": {"Title": 0.16, "KeyMessage": 0.2, "Visual": 0.55, "Footer": 0.09},
        "text_visual": {"text": 0.4, "visual": 0.6},
        "when_to_use": "마무리/CTA — 핵심 메시지 + 연락/행동 유도",
    },
]


def _normalize_ratios(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values()) or 1.0
    return {k: round(v / total, 4) for k, v in weights.items()}


def build_blueprints() -> list[dict]:
    out: list[dict] = []
    for i, bp in enumerate(_BLUEPRINTS):
        region_ratios = _normalize_ratios(bp["weights"])
        visual_share = round(sum(r for c, r in region_ratios.items() if c in _VISUAL_REGIONS), 4)
        out.append({
            "blueprint_id": f"{bp['intent']}_{i:02d}",
            "intent": bp["intent"],
            "genre": bp["genre"],
            "layout_type": "slide",
            "section_order": bp["section_order"],
            "region_ratios": region_ratios,
            "visual_area_share": visual_share,            # region 기준 실제 시각 면적 비율
            "text_visual_ratio": bp["text_visual"],       # 디자인 목표 비율
            "when_to_use": bp["when_to_use"],
            "source": "curated",
            "license_status": "open",                     # 자체 큐레이션(외부 데이터 아님)
        })
    return out


def build_summary(bps: list[dict]) -> dict:
    by_intent = Counter(b["intent"] for b in bps)
    genre_index: dict[str, list[str]] = defaultdict(list)
    region_freq: Counter = Counter()
    visual_shares: list[float] = []
    for b in bps:
        for g in b["genre"]:
            genre_index[g].append(b["blueprint_id"])
        for c in b["region_ratios"]:
            region_freq[c] += 1
        visual_shares.append(b["visual_area_share"])
    return {
        "total_blueprints": len(bps),
        "intents": dict(by_intent),
        "genre_index": {g: ids for g, ids in genre_index.items()},
        "region_frequency": dict(region_freq.most_common()),
        "avg_visual_area_share": round(sum(visual_shares) / len(visual_shares), 4) if bps else 0.0,
        "design_principle": {
            "target": "Visual ~65-75% / Text ~25-35% (구조 우선, 카드 나열 탈피)",
            "note": "DocLayNet 실측: Table 0.29·Text 0.25·Picture 0.22 → 본 라이브러리는 visual-heavy 보정",
        },
    }


_README = """# 프레젠테이션 Blueprint 패턴 데이터셋

교육청 제안서·사업계획서·회사소개서·전략보고서용 **의도(intent)별 슬라이드 레이아웃 블루프린트**.
디자인(컬러/폰트)이 아니라 **구조(배치순서·영역비율)** 데이터. Layout Pattern Engine /
Blueprint Recommendation Engine 학습·추천용.

## 파일
- `presentation_blueprints.jsonl` — 블루프린트 1건/1줄.
- `summary.json` — 의도 목록, 장르 인덱스(genre→blueprint_id), 영역 빈도, 평균 visual 면적비율, 설계 원칙.
- `README.md` — 이 문서.

## blueprint 스키마
```json
{
  "blueprint_id": "architecture_05",
  "intent": "architecture",                 // cover/executive_summary/roadmap/...
  "genre": ["proposal","strategy_report"],
  "layout_type": "slide",
  "section_order": ["Title","Diagram","Caption","Callout"],   // 읽기 순서
  "region_ratios": {"Title":0.09,"Diagram":0.70,...},          // 합 1.0
  "visual_area_share": 0.70,                 // 시각영역 면적 비율(구조 기준)
  "text_visual_ratio": {"text":0.25,"visual":0.75},            // 디자인 목표
  "when_to_use": "..."
}
```

## 추천 엔진 사용
1. 슬라이드 의도(예: "Architecture") 입력 → `intent`/`genre`로 후보 블루프린트 검색
2. `section_order`로 영역 배치, `region_ratios`로 크기 배분
3. `text_visual_ratio`로 "Visual 우선" 강제 → 카드 나열(Text 70%) 탈피

## 핵심 설계 원칙
좋은 제안/보고 문서는 **Visual ~75% / Text ~25%**. 본 라이브러리는 visual-heavy로 보정해
구조 우선 레이아웃을 권장한다(예외: agenda/risk 등 텍스트 밀도 슬라이드).

⚠️ 자체 큐레이션 데이터(외부 데이터셋 원본 미포함). 자유 활용.
"""


def main() -> int:
    p = argparse.ArgumentParser(description="프레젠테이션 Blueprint 패턴 데이터셋 생성 → zip")
    p.add_argument("--output", default="presentation_blueprints.zip", help="출력 zip 경로")
    args = p.parse_args()

    bps = build_blueprints()
    summary = build_summary(bps)

    jsonl = io.StringIO()
    for b in bps:
        jsonl.write(json.dumps(b, ensure_ascii=False) + "\n")

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("presentation_blueprints.jsonl", jsonl.getvalue())
        zf.writestr("summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
        zf.writestr("README.md", _README)

    size = os.path.getsize(args.output)
    print(f"[gen] {len(bps)} blueprints → {args.output} ({size/1024:.1f} KB)")
    print(f"[gen] intents={list(summary['intents'])}")
    print(f"[gen] 평균 visual 면적비율={summary['avg_visual_area_share']}")
    print(f"[gen] 장르 인덱스: " + ", ".join(f"{g}:{len(ids)}" for g, ids in summary["genre_index"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())