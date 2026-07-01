# UPE Web Source Collector

[![CI](https://github.com/ryu-daehyun82/upe-web-source-collector/actions/workflows/ci.yml/badge.svg)](https://github.com/ryu-daehyun82/upe-web-source-collector/actions/workflows/ci.yml)

> Universal Pattern Extractor(UPE)의 **웹 패턴 거버넌스 파이프라인**.
> 스크래퍼가 아니라 — 허용된 원천에서 **구조적 패턴만** 추출하고, 원본 표현(저작권/PII) 재사용 위험을 차단·감사·삭제할 수 있는 프로덕션 인프라.

설계서: `~/Downloads/UPE_Web_Source_Collector_프로덕션_마스터설계서_v2.0_2026-06-29.md`
패치: `UPE_Web_Source_Collector_v2.1_설계패치_2026-06-29.md` · Risk Score: `UPE_ReuseRiskScore_설계스파이크_2026-06-29.md`

## 파이프라인

```
Seed → Frontier → Web Source API → Policy Gate(robots/license/PII)
     → Crawl Job Queue → Worker(HTTP/Playwright/File) → Snapshot Store
     → Parser → Pattern Abstraction Guard(Reuse Risk Score) → PatternDB
```

## 상태기계 (3종)

- **Source**: discovered → policy_pending → allowed_crawl → crawled → parsed → pattern_built → approved_for_pattern_use → (blocked/delete_requested/deleted)
- **Crawl Job**: queued → policy_checking → ready → running → succeeded / failed_retryable / failed_terminal / cancelled
- **Pattern**: built → abstraction_checked → reuse_risk_scored → approved / blocked / deprecated

## 구현 현황 (496 tests green)

설계서 v2.0/v2.1/스파이크의 핵심 워크플로우가 구현·테스트·실사용 검증됨.

**수집 전(pre-fetch)** — `crawl_planner.CrawlPlanner`
- `frontier`(canon·dedup·depth/domain·sitemap) → `robots_checker`(SSRF 차단) → `redis_backend`(politeness 토큰버킷) → `jobs`(멱등 enqueue)

**워커(§8, 동일 WebWorker 계약)** — `runner.process_crawl_job`
- `http_worker`(정적) · `playwright_worker`(동적, 우회 차단) · `pdf_worker`/`file_parse_worker`(바이너리)
- 파서: `html_parser`/`pdf_parser`/`ppt_parser`/`image_parser`/`video_parser`(어댑터+폴백)

**거버넌스(§9, 핵심 IP)** — `pipeline.run_pattern_governance`
- `abstraction_guard`(원본 제거) → PII(`adapters/pii`) → 시각검출(`adapters/vision`) → `reuse_risk`(하드룰+가중합) → `reconstruction_test`(G4) → `GovernanceDecision`
- 보정: `golden_set`(critical recall=100% 릴리즈 게이트), `brand_risk_lookup`

**영속화·상태·운영**
- `pattern_build`(web_patterns, `_leak_probe` 원문 제외) · `job_state`(상태 전이) · `retry_policy`(§7.3) · `operational_gate`(§4.3)
- `events`(§7 Kafka 토픽) · `metrics`(§12 KPI/SLO/Alert) · `delete_recheck`(§11/§6.5) · `rbac`(§13.3)

**API(§6)** — `api/web_sources`(등록/policy-check/delete) + `api/operations`(crawl-jobs/pattern approve·block/apply-delete/recheck, RBAC 강제)

## 스택

Python 3.11 / FastAPI / SQLAlchemy(async) / PostgreSQL+pgvector(운영)·sqlite(테스트).
운영 백엔드는 지역 import 어댑터 — Redis · Kafka(aiokafka) · Playwright · Prometheus · 테스트는 폴백(InMemory/fakeredis/가짜).

## 실행

```bash
pip install -r requirements.txt
psql < sql/001_init.sql        # DDL
uvicorn app.main:app --reload  # API
pytest tests/                  # 496 tests
```

## 패턴 데이터셋 (수집·내보내기·블루프린트·검증)

외부 데이터셋(DocLayNet·PubLayNet=CDLA-Permissive, AIHub 차트=비상업)을 거버넌스 통과시켜
구조 패턴만 적재하고, 의도별 블루프린트·검증팩으로 내보낸다. 원본 표현은 미저장.

```bash
# 적재: 데이터셋 → 거버넌스 → web_patterns
python scripts/ingest_patterns.py --source doclaynet   --input COCO/test.json --create-tables
python scripts/ingest_patterns.py --source aihub_chart --input charts/            # AIHub 차트(재귀 글롭)
python scripts/ingest_publaynet_hf.py --local-glob 'shards/train-*.parquet'       # PubLayNet parquet

# 내보내기: web_patterns → GPT 활용 zip(jsonl+summary)
python scripts/export_patterns.py --db-url sqlite+aiosqlite:///x.db --output patterns.zip

# 블루프린트: 의도별 추천(슬라이드/차트) — DocLayNet 실측으로 보정
python scripts/gen_presentation_blueprints.py --output slides.zip
python scripts/calibrate_blueprints.py --summary <export summary.json> --output slides_calibrated.zip
python scripts/gen_chart_blueprints.py --patterns <chart patterns.jsonl> --output charts.zip
```

### 검증팩 빌더 (원문/재현 + 추출 패턴)

패턴 추출이 원문과 맞는지 육안 검증하는 팩(원문/재현 이미지 + bbox 오버레이/코드 + `pattern.json`).
`--source` 로 3 출처 dispatch. 무거운 의존성(PIL·pyarrow·matplotlib·remotezip)은 지연 임포트.

```bash
# PubLayNet: parquet(이미지 내장) → 원문+오버레이+패턴
python scripts/build_verification_pack.py --source publaynet \
  --parquet-glob 'shards/train-*.parquet' --limit 100 --output pub.zip

# DocLayNet: COCO + 로컬 페이지 PNG → 원문+오버레이+패턴
python scripts/build_verification_pack.py --source doclaynet \
  --coco COCO/test.json --png-dir dln_png/PNG --limit 80 --output dln.zip

# AIHub 차트: visualize_code 재현(원문 아님) + 코드 + 패턴
python scripts/build_verification_pack.py --source aihub_chart \
  --input charts/ --per-type 15 --output chart.zip     # 유형별(bar/line/pie/mixed) 개수
```

| `--source` | 필수 인자 | 원문 | 라이선스 |
|---|---|---|---|
| `publaynet` | `--parquet-glob`, `--limit` | 페이지 이미지 | CDLA-Permissive(재배포 OK) |
| `doclaynet` | `--coco`, `--png-dir`, `--limit` | 페이지 이미지 | CDLA-Permissive(재배포 OK) |
| `aihub_chart` | `--input`, `--per-type`, `--font` | **재현**(원문 미배포) | AIHub 비상업 |

검증: `_overlay`/`_rendered` 이미지의 영역·순서·차트유형 ↔ `_pattern.json`(section_order·region_ratios·chart_type) 대조.

## 재사용 (보유 자산 어댑터 — 인터페이스 고정, 폴백 동작)

PII=harvester `pii_filter` · dedup=harvester `dedup` · PDF=`pdf-layer-rebuilder-mcp` · layout/color=`gcr-eare` · face=`makeup-ai-py identity_guard` · 이미지=Pillow · 영상=ffprobe. 복붙 금지, 어댑터 import(미존재 시 self-contained 폴백).

## ⚠️ 원칙

robots/terms/login/paywall/CAPTCHA 우회 금지 · raw snapshot 접근 제한 · 정책 게이트 통과 없이는 수집 금지 · high/blocked reuse risk 패턴은 생성엔진 전달 금지.
