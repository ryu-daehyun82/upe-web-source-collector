"""DocLayNet 실측 비율로 프레젠테이션 Blueprint 자동 보정.

원리: 실제 문서에서 Title/Text/Footer/Table 등 **구조 영역의 면적은 작고 일정**하다
(DocLayNet 실측: Title 0.028·Text 0.25·Page-footer 0.002·Table 0.29…). 블루프린트의
비-시각(non-visual) 영역 가중치를 이 실측 prior 로 블렌딩(alpha)하면, 텍스트/제목이
현실적으로 압축되고 **남는 공간이 시각영역으로 재배분** → "Visual-heavy" 목표가 강화된다.
시각영역과 프레젠테이션 전용영역(Metric/Callout 등)은 디자인 의도대로 유지.

priors 출처: --summary(export 한 doclaynet summary.json 의 avg_region_ratio_by_category)
없으면 내장 측정 상수(테스트 4994페이지). 출력: 보정 blueprint zip.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gen_presentation_blueprints import build_blueprints, _VISUAL_REGIONS, _normalize_ratios  # noqa: E402

# DocLayNet 실측 평균 영역비율(테스트 4994페이지) — --summary 미지정 시 기본 prior.
_DOCLAYNET_PRIORS: dict[str, float] = {
    "Title": 0.0278, "Section-header": 0.0138, "Text": 0.251, "List-item": 0.1128,
    "Caption": 0.0183, "Footnote": 0.0304, "Table": 0.2905, "Picture": 0.2224,
    "Page-header": 0.0048, "Page-footer": 0.0017, "Formula": 0.0929,
}

# 블루프린트 영역 → DocLayNet 카테고리 매핑(비-시각 구조 영역만). 시각·전용영역은 미보정.
_REGION_TO_DOCNET: dict[str, str] = {
    "Title": "Title",
    "Subtitle": "Section-header",
    "KeyMessage": "Text",
    "Body": "Text",
    "BulletList": "List-item",
    "Caption": "Caption",
    "Footnote": "Footnote",
    "Footer": "Page-footer",
    "Table": "Table",
}


def load_priors(summary_path: str | None) -> dict[str, float]:
    """export summary.json 의 avg_region_ratio_by_category 사용, 없으면 내장 상수."""
    if summary_path and os.path.exists(summary_path):
        with open(summary_path, encoding="utf-8") as f:
            s = json.load(f)
        pri = s.get("avg_region_ratio_by_category")
        if isinstance(pri, dict) and pri:
            return {k: float(v) for k, v in pri.items()}
    return dict(_DOCLAYNET_PRIORS)


def calibrate_blueprint(bp: dict, priors: dict[str, float], *, alpha: float) -> dict:
    """비-시각 매핑 영역을 prior 로 블렌딩 후 재정규화. 보정 메타 포함 dict 반환."""
    design = bp["region_ratios"]
    blended: dict[str, float] = {}
    for region, w in design.items():
        cat = _REGION_TO_DOCNET.get(region)
        if region not in _VISUAL_REGIONS and cat is not None and cat in priors:
            blended[region] = (1.0 - alpha) * w + alpha * priors[cat]
        else:
            blended[region] = w
    calibrated = _normalize_ratios(blended)
    visual_share = round(sum(r for c, r in calibrated.items() if c in _VISUAL_REGIONS), 4)

    out = dict(bp)
    out["region_ratios_design"] = design
    out["region_ratios"] = calibrated
    out["visual_area_share"] = visual_share
    out["calibration"] = {"from": "doclaynet", "alpha": alpha}
    return out


def build_summary(bps_design: list[dict], bps_cal: list[dict], *, alpha: float, priors: dict[str, float]) -> dict:
    genre_index: dict[str, list[str]] = defaultdict(list)
    intents = Counter()
    for b in bps_cal:
        intents[b["intent"]] += 1
        for g in b["genre"]:
            genre_index[g].append(b["blueprint_id"])
    avg_before = round(sum(b["visual_area_share"] for b in bps_design) / len(bps_design), 4)
    avg_after = round(sum(b["visual_area_share"] for b in bps_cal) / len(bps_cal), 4)
    return {
        "total_blueprints": len(bps_cal),
        "calibration": {"from": "doclaynet", "alpha": alpha, "priors_used": priors},
        "avg_visual_area_share_before": avg_before,
        "avg_visual_area_share_after": avg_after,
        "intents": dict(intents),
        "genre_index": dict(genre_index),
        "note": "비-시각 구조영역(Title/Text/Footer/Table…)을 DocLayNet 실측치로 압축 → "
                "남는 면적이 시각영역으로 재배분되어 visual_area_share 상승(구조 우선·visual-heavy 강화).",
    }


_README = """# 프레젠테이션 Blueprint (DocLayNet 실측 보정본)

`presentation_blueprints` 를 **DocLayNet 실측 영역비율로 자동 보정**한 데이터셋.
비-시각 구조영역(Title/Text/Footer/Table/List-item/Caption/Footnote)을 실측 prior 로
블렌딩(alpha)해 현실적 크기로 압축 → 남는 면적이 시각영역으로 가서 visual-heavy 강화.

## 파일
- `blueprints_calibrated.jsonl` — 각 블루프린트에 보정 `region_ratios` +
  원본 `region_ratios_design` + `calibration{from,alpha}` 포함.
- `summary.json` — alpha·prior, 보정 전/후 평균 visual 면적, 의도/장르 인덱스.
- `README.md`.

## 스키마(보정 추가분)
- `region_ratios` — **보정 후**(합 1.0)
- `region_ratios_design` — 보정 전(원 디자인)
- `visual_area_share` — 보정 후 시각영역 면적비율
- `calibration` — {{"from":"doclaynet","alpha":0.4}}

## 의미
실측 문서는 제목·본문·푸터가 작고 일정. 그 현실을 반영하되 시각영역은 디자인 의도대로 두면,
"구조는 현실적, 비주얼은 강하게"가 동시에 성립한다. Layout Pattern Engine 추천 시
이 보정본을 기본값으로, 원본(design)은 비교/튜닝용으로 사용 권장.
"""


def main() -> int:
    p = argparse.ArgumentParser(description="DocLayNet 실측 비율로 blueprint 보정 → zip")
    p.add_argument("--summary", default=None, help="export summary.json 경로(avg_region_ratio_by_category)")
    p.add_argument("--alpha", type=float, default=0.4, help="블렌딩 강도(0=디자인유지, 1=실측우선)")
    p.add_argument("--output", default="presentation_blueprints_calibrated.zip", help="출력 zip")
    args = p.parse_args()

    priors = load_priors(args.summary)
    design = build_blueprints()
    calibrated = [calibrate_blueprint(b, priors, alpha=args.alpha) for b in design]
    summary = build_summary(design, calibrated, alpha=args.alpha, priors=priors)

    jsonl = io.StringIO()
    for b in calibrated:
        jsonl.write(json.dumps(b, ensure_ascii=False) + "\n")
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("blueprints_calibrated.jsonl", jsonl.getvalue())
        zf.writestr("summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
        zf.writestr("README.md", _README)

    print(f"[calibrate] {len(calibrated)} blueprints, alpha={args.alpha} → {args.output} "
          f"({os.path.getsize(args.output)/1024:.1f} KB)")
    print(f"[calibrate] priors source={'summary.json' if args.summary else '내장 측정상수'} "
          f"({len(priors)} categories)")
    print(f"[calibrate] visual_area_share 평균: 보정전 {summary['avg_visual_area_share_before']} "
          f"→ 보정후 {summary['avg_visual_area_share_after']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())