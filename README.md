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

## 구현 현황 (422 tests green)

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
pytest tests/                  # 422 tests
```

## 재사용 (보유 자산 어댑터 — 인터페이스 고정, 폴백 동작)

PII=harvester `pii_filter` · dedup=harvester `dedup` · PDF=`pdf-layer-rebuilder-mcp` · layout/color=`gcr-eare` · face=`makeup-ai-py identity_guard` · 이미지=Pillow · 영상=ffprobe. 복붙 금지, 어댑터 import(미존재 시 self-contained 폴백).

## ⚠️ 원칙

robots/terms/login/paywall/CAPTCHA 우회 금지 · raw snapshot 접근 제한 · 정책 게이트 통과 없이는 수집 금지 · high/blocked reuse risk 패턴은 생성엔진 전달 금지.
