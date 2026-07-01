"""검증팩 빌더 — 원문/재현 이미지 + 추출 패턴을 zip으로.

패턴 추출이 원문과 맞는지 육안 검증하기 위한 팩. 3 출처:
  - doclaynet    : DocLayNet 페이지 PNG + bbox 오버레이 + 패턴 (CDLA-Permissive, 원문 OK)
  - publaynet    : PubLayNet parquet(이미지 내장) + 오버레이 + 패턴 (CDLA-Permissive)
  - aihub_chart  : AIHub 차트 라벨의 visualize_code 를 **재현**(원문 아님) + 코드 + 패턴 (비상업)

무거운 의존성(PIL/pyarrow/matplotlib/remotezip)은 지연 임포트. 순수 헬퍼
(unescape_viz_code/sanitize_font_path/row_to_regions)는 단위 테스트 대상.

사용:
  python scripts/build_verification_pack.py --source publaynet \
    --parquet-glob 'shards/train-*.parquet' --limit 100 --output pub.zip
  python scripts/build_verification_pack.py --source aihub_chart \
    --input charts/ --per-type 15 --output chart.zip
  python scripts/build_verification_pack.py --source doclaynet \
    --coco COCO/test.json --png-dir dln_png/PNG --limit 80 --output dln.zip
"""
from __future__ import annotations

import argparse
import contextlib
import glob
import io
import json
import os
import re
import sys
import tempfile
import zipfile
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.workers.doclaynet_parser import parse_doclaynet_page  # noqa: E402
from app.workers.aihub_chart_parser import parse_aihub_chart  # noqa: E402

DEFAULT_FONT = "/System/Library/Fonts/Supplemental/AppleGothic.ttf"

DOC_COLORS = {
    "Caption": "#14b8a6", "Footnote": "#64748b", "Formula": "#ec4899", "List-item": "#22c55e",
    "Page-footer": "#94a3b8", "Page-header": "#94a3b8", "Picture": "#a855f7",
    "Section-header": "#ef4444", "Table": "#f59e0b", "Text": "#3b82f6", "Title": "#dc2626",
}
PUB_COLORS = {"text": "#3b82f6", "title": "#ef4444", "list": "#22c55e", "table": "#f59e0b", "figure": "#a855f7"}


# ────────────────── 순수 헬퍼(테스트 대상) ──────────────────
def row_to_regions(bboxes, labels) -> list[dict]:
    """(bboxes, labels) → regions[{category,bbox[x,y,w,h]}]. bbox xyxy면 wh로 변환."""
    regions: list[dict] = []
    for bb, lab in zip(bboxes, labels):
        if bb is None or len(bb) < 4:
            continue
        x1, y1, x2, y2 = bb[0], bb[1], bb[2], bb[3]
        w, h = (x2 - x1, y2 - y1) if (x2 > x1 and y2 > y1) else (x2, y2)
        regions.append({"category": str(lab), "bbox": [x1, y1, w, h]})
    return regions


def unescape_viz_code(raw: str) -> str:
    """visualize_code 의 리터럴 이스케이프 정규화.

    문 구분자로 쓰인 backslash-n/backslash-t 는 실제 개행/탭으로 바꾸되,
    **문자열 리터럴 내부**의 이스케이프는 보존(라벨의 '\\n' 등이 깨지지 않게).
    """
    out: list[str] = []
    i, n, quote = 0, len(raw), None
    while i < n:
        c = raw[i]
        if quote:
            if c == "\\" and i + 1 < n:      # 문자열 내부 이스케이프는 그대로 보존
                out.append(c); out.append(raw[i + 1]); i += 2; continue
            if c == quote:
                quote = None
            out.append(c); i += 1
        else:
            if c in ("'", '"'):
                quote = c; out.append(c); i += 1
            elif c == "\\" and i + 1 < n and raw[i + 1] == "n":
                out.append("\n"); i += 2
            elif c == "\\" and i + 1 < n and raw[i + 1] == "t":
                out.append("\t"); i += 2
            else:
                out.append(c); i += 1
    return "".join(out)


def sanitize_font_path(code: str, font: str) -> str:
    """visualize_code 의 존재하지 않는 /app/font/*.ttf 경로를 실제 폰트로 전역 치환."""
    return re.sub(r"/app/font/[^'\"]*\.ttf", font, code)


# ────────────────── 이미지 유틸(지연 임포트) ──────────────────
def _overlay(img, regions: list[dict], colors: dict):
    from PIL import ImageDraw
    ov = img.copy(); dr = ImageDraw.Draw(ov)
    for r in regions:
        x, y, bw, bh = r["bbox"]
        c = colors.get(str(r["category"]), colors.get(str(r["category"]).lower(), "#888"))
        dr.rectangle([x, y, x + bw, y + bh], outline=c, width=3)
        dr.text((x + 2, y + 2), str(r["category"]), fill=c)
    return ov


def _jpeg(img, q=80) -> bytes:
    b = io.BytesIO(); img.convert("RGB").save(b, "JPEG", quality=q); return b.getvalue()


def render_viz_code(raw: str, font: str):
    """visualize_code 실행 → PNG bytes(재현). 실패 시 (None, None)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    code = sanitize_font_path(unescape_viz_code(raw), font)
    cap: dict = {}
    orig = plt.savefig

    def mysave(*a, **k):
        b = io.BytesIO(); orig(b, format="png", dpi=90, bbox_inches="tight"); cap["b"] = b.getvalue()

    plt.savefig = mysave
    cwd = os.getcwd(); os.chdir(tempfile.mkdtemp())
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(code, "<viz>", "exec"), {"__name__": "__viz__"})
        return cap.get("b"), code
    except Exception:
        return None, None
    finally:
        plt.savefig = orig; plt.close("all"); os.chdir(cwd)


# ────────────────── 출처별 빌드 ──────────────────
def build_publaynet(zf, *, parquet_glob: str, limit: int) -> int:
    import pyarrow.parquet as pq
    from PIL import Image
    path = sorted(glob.glob(parquet_glob))[0]
    cols = set(pq.ParquetFile(path).schema_arrow.names)
    bcol = "bboxes" if "bboxes" in cols else "bbox"
    lcol = "labels" if "labels" in cols else "categories"
    idcol = "file_id" if "file_id" in cols else ("id" if "id" in cols else None)
    read_cols = ["image", bcol, lcol] + ([idcol] if idcol else [])
    t = pq.read_table(path, columns=read_cols).to_pydict()
    manifest = []
    for i in range(min(limit, len(t["image"]))):
        img = Image.open(io.BytesIO(t["image"][i]["bytes"]))
        w, h = img.size
        regions = row_to_regions(t[bcol][i], t[lcol][i])
        feat = parse_doclaynet_page(w, h, regions)
        fid = str(t[idcol][i]).replace(".jpg", "") if idcol else str(i)
        zf.writestr(f"samples/{i:03d}_{fid}_original.jpg", _jpeg(img))
        zf.writestr(f"samples/{i:03d}_{fid}_overlay.jpg", _jpeg(_overlay(img, regions, PUB_COLORS)))
        zf.writestr(f"samples/{i:03d}_{fid}_pattern.json", json.dumps(feat, ensure_ascii=False, indent=2))
        manifest.append({"idx": i, "file_id": fid, "section_order": feat["section_order"]})
    zf.writestr("README.md", "# PubLayNet 검증팩\nCDLA-Permissive-1.0(PMC). _original/_overlay/_pattern. "
                "검증: overlay 박스·순서 ↔ pattern.json section_order. text파랑 title빨강 list초록 table주황 figure보라.")
    zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return len(manifest)


def build_doclaynet(zf, *, coco: str, png_dir: str, limit: int) -> int:
    from PIL import Image
    d = json.load(open(coco, encoding="utf-8"))
    catname = {c["id"]: c["name"] for c in d["categories"]}
    byimg: dict = defaultdict(list)
    for a in d["annotations"]:
        byimg[a["image_id"]].append(a)
    have = {os.path.basename(p) for p in glob.glob(os.path.join(png_dir, "*.png"))}
    sel = [im for im in d["images"] if im["file_name"] in have][:limit]
    manifest = []; ok = 0
    for im in sel:
        p = os.path.join(png_dir, im["file_name"])
        try:
            if os.path.getsize(p) < 1000:
                continue
            img = Image.open(p); img.load()
        except Exception:
            continue
        w, h = img.size
        regions = [{"category": catname[a["category_id"]], "bbox": a["bbox"]} for a in byimg[im["id"]]]
        feat = parse_doclaynet_page(w, h, regions)
        fid = im["file_name"][:12]
        zf.writestr(f"samples/{ok:03d}_{fid}_original.jpg", _jpeg(img))
        zf.writestr(f"samples/{ok:03d}_{fid}_overlay.jpg", _jpeg(_overlay(img, regions, DOC_COLORS)))
        zf.writestr(f"samples/{ok:03d}_{fid}_pattern.json", json.dumps(feat, ensure_ascii=False, indent=2))
        manifest.append({"idx": ok, "file": im["file_name"], "section_order": feat["section_order"]}); ok += 1
    zf.writestr("README.md", "# DocLayNet 검증팩\nCDLA-Permissive-2.0. _original/_overlay/_pattern. "
                "검증: overlay 박스·순서 ↔ pattern.json. Text파랑 Title/Section-header빨강 Table주황 Picture보라.")
    zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return ok


def build_aihub_chart(zf, *, input_dir: str, per_type: int, font: str) -> int:
    files = sorted(glob.glob(os.path.join(input_dir, "**", "*.json"), recursive=True))
    want = {"bar": per_type, "line": per_type, "pie": per_type, "mixed": per_type}
    got = {k: 0 for k in want}; manifest = []; idx = 0
    for f in files:
        if sum(got.values()) >= sum(want.values()):
            break
        d = json.load(open(f, encoding="utf-8"))
        feat = parse_aihub_chart(d); t = feat["chart_type"]
        if got.get(t, 99) >= want.get(t, 0):
            continue
        img, code = render_viz_code(d.get("visualize_code", ""), font)
        if not img:
            continue
        zf.writestr(f"samples/{idx:03d}_{t}_rendered.png", img)
        zf.writestr(f"samples/{idx:03d}_{t}_pattern.json", json.dumps(feat, ensure_ascii=False, indent=2))
        zf.writestr(f"samples/{idx:03d}_{t}_code.py", code)
        manifest.append({"idx": idx, "chart_type": t, "category_count": feat["category_count"],
                         "series_count": feat["series_count"], "color_count": feat["color_count"]})
        got[t] += 1; idx += 1
    zf.writestr("README.md", "# AIHub 차트 검증팩\n⚠️ 원문 아님 — visualize_code 재현. _rendered.png/_pattern.json/_code.py. "
                "검증: 재현차트 유형·범주수·색수·범례 ↔ pattern.json.")
    zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return len(manifest)


def main() -> int:
    p = argparse.ArgumentParser(description="검증팩(원문/재현+패턴) 빌더")
    p.add_argument("--source", required=True, choices=["doclaynet", "publaynet", "aihub_chart"])
    p.add_argument("--output", required=True)
    p.add_argument("--limit", type=int, default=100, help="doclaynet/publaynet 샘플 수")
    p.add_argument("--per-type", type=int, default=15, help="aihub_chart 유형별 샘플 수")
    p.add_argument("--coco", default=None, help="doclaynet COCO json")
    p.add_argument("--png-dir", default=None, help="doclaynet 페이지 PNG 디렉터리")
    p.add_argument("--parquet-glob", default=None, help="publaynet parquet glob")
    p.add_argument("--input", default=None, help="aihub_chart JSON 디렉터리")
    p.add_argument("--font", default=DEFAULT_FONT, help="차트 재현용 한글 폰트")
    args = p.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as zf:
        if args.source == "publaynet":
            assert args.parquet_glob, "--parquet-glob 필요"
            n = build_publaynet(zf, parquet_glob=args.parquet_glob, limit=args.limit)
        elif args.source == "doclaynet":
            assert args.coco and args.png_dir, "--coco, --png-dir 필요"
            n = build_doclaynet(zf, coco=args.coco, png_dir=args.png_dir, limit=args.limit)
        else:
            assert args.input, "--input 필요"
            n = build_aihub_chart(zf, input_dir=args.input, per_type=args.per_type, font=args.font)
    print(f"[verify-pack] {args.source}: {n}샘플 → {args.output} ({os.path.getsize(args.output)/1024/1024:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())