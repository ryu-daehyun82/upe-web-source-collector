# UPE Web Source Collector

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

## Sprint 0 범위 (현재)

Policy-first 골격 — web_sources·crawl_policies DDL · Source 등록 API · robots checker · license 상태기계.

## 스택

Python 3.11 / FastAPI / PostgreSQL+pgvector / (Kafka·Redis·Playwright는 Sprint 5).

## 실행

```bash
pip install -r requirements.txt
psql < sql/001_init.sql        # DDL
uvicorn app.main:app --reload  # API
pytest tests/
```

## 재사용 (보유 자산 어댑터)

PII=harvester `pii_filter` · dedup=harvester `dedup` · PDF=`pdf-layer-rebuilder-mcp` · layout/color=`gcr-eare` · face=`makeup-ai-py identity_guard`. 복붙 금지, 어댑터 import.

## ⚠️ 원칙

robots/terms/login/paywall/CAPTCHA 우회 금지 · raw snapshot 접근 제한 · 정책 게이트 통과 없이는 수집 금지 · high/blocked reuse risk 패턴은 생성엔진 전달 금지.
