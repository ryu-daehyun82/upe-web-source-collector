"""관측성 메트릭 (설계 §12).

어댑터+폴백 수집기(InMemory/Null/Prometheus) + §12.1 KPI · §12.2 SLO(p95) · §12.3 Alert 계산.
prometheus_client 는 PrometheusMetrics 내부 지역 import(미설치여도 모듈 import 안 깨짐).
"""
from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass


# 성공/실패 카운터
M_POLICY_CHECK_SUCCESS = "policy_check_success"
M_POLICY_CHECK_FAILED = "policy_check_failed"
M_CRAWL_SUCCESS = "crawl_success"
M_CRAWL_FAILED = "crawl_failed"
M_PARSE_SUCCESS = "parse_success"
M_PARSE_FAILED = "parse_failed"
# 패턴/거버넌스
M_PATTERN_BUILT = "pattern_built"
M_PATTERN_APPROVED = "pattern_approved"
M_PATTERN_BLOCKED = "pattern_blocked"
M_PII_BLOCKED = "pii_blocked"
M_REUSE_BLOCKED = "reuse_blocked"
M_DUPLICATE = "duplicate"
M_PROCESSED = "processed"          # 처리 총량(비율 분모)
M_LICENSE_MANUAL_REVIEW = "license_manual_review"
M_LICENSE_TOTAL = "license_total"

# SLO 타이머(ms) 이름
T_URL_REGISTER = "url_register"
T_POLICY_CHECK = "policy_check"
T_STATIC_FETCH = "static_fetch"
T_PLAYWRIGHT_RENDER = "playwright_render"
T_PDF_PARSE = "pdf_parse"
T_PATTERN_ABSTRACTION = "pattern_abstraction"
T_DELETE_ACTION = "delete_action"

SLO_TARGETS_MS: dict[str, int] = {
    T_URL_REGISTER: 300, T_POLICY_CHECK: 5000, T_STATIC_FETCH: 30000,
    T_PLAYWRIGHT_RENDER: 60000, T_PDF_PARSE: 120000, T_PATTERN_ABSTRACTION: 30000,
    T_DELETE_ACTION: 5000,
}


class InMemoryMetrics:
    """테스트/로컬 메트릭 — 카운터 + 타이밍 기록."""

    def __init__(self) -> None:
        self._counters: dict[str, float] = {}
        self._timings: dict[str, list[float]] = {}

    def inc(self, name: str, value: float = 1.0) -> None:
        self._counters[name] = self._counters.get(name, 0.0) + value

    def observe(self, name: str, value_ms: float) -> None:
        self._timings.setdefault(name, []).append(value_ms)

    def counter(self, name: str) -> float:
        return self._counters.get(name, 0.0)

    def timings(self, name: str) -> list[float]:
        return list(self._timings.get(name, []))

    def percentile(self, name: str, p: float) -> float:
        """nearest-rank 백분위. 빈 리스트 0.0. p ∈ [0,100]."""
        vals = sorted(self.timings(name))
        n = len(vals)
        if n == 0:
            return 0.0
        idx = max(0, math.ceil(p / 100.0 * n) - 1)
        idx = min(idx, n - 1)
        return vals[idx]

    def reset(self) -> None:
        self._counters.clear()
        self._timings.clear()


class NullMetrics:
    """메트릭 비활성(no-op)."""

    def inc(self, name: str, value: float = 1.0) -> None:
        pass

    def observe(self, name: str, value_ms: float) -> None:
        pass


class PrometheusMetrics:
    """실 Prometheus(prometheus_client). 지역 import — 미설치 시 inc/observe 호출에서만 필요."""

    def __init__(self, namespace: str = "upe") -> None:
        self.namespace = namespace
        self._counters: dict = {}
        self._hists: dict = {}

    def inc(self, name: str, value: float = 1.0) -> None:
        from prometheus_client import Counter

        c = self._counters.get(name)
        if c is None:
            c = Counter(f"{self.namespace}_{name}", name)
            self._counters[name] = c
        c.inc(value)

    def observe(self, name: str, value_ms: float) -> None:
        from prometheus_client import Histogram

        h = self._hists.get(name)
        if h is None:
            h = Histogram(f"{self.namespace}_{name}_ms", name)
            self._hists[name] = h
        h.observe(value_ms)


@contextmanager
def timer(metrics, name: str):
    """작업 소요시간(ms)을 metrics.observe(name, …)로 기록."""
    import time

    start = time.perf_counter()
    try:
        yield
    finally:
        metrics.observe(name, (time.perf_counter() - start) * 1000.0)


def _rate(num: float, den: float) -> float:
    """비율. 분모<=0 이면 0.0."""
    return num / den if den > 0 else 0.0


def compute_kpis(m: InMemoryMetrics) -> dict[str, float]:
    """§12.1 KPI 비율(0~1)."""
    policy_succ = m.counter(M_POLICY_CHECK_SUCCESS)
    policy_fail = m.counter(M_POLICY_CHECK_FAILED)
    crawl_succ = m.counter(M_CRAWL_SUCCESS)
    crawl_fail = m.counter(M_CRAWL_FAILED)
    parse_succ = m.counter(M_PARSE_SUCCESS)
    parse_fail = m.counter(M_PARSE_FAILED)
    built = m.counter(M_PATTERN_BUILT)
    approved = m.counter(M_PATTERN_APPROVED)
    reuse_blocked = m.counter(M_REUSE_BLOCKED)
    pii_blocked = m.counter(M_PII_BLOCKED)
    duplicate = m.counter(M_DUPLICATE)
    processed = m.counter(M_PROCESSED)
    license_manual = m.counter(M_LICENSE_MANUAL_REVIEW)
    license_total = m.counter(M_LICENSE_TOTAL)

    return {
        "policy_check_success_rate": _rate(policy_succ, policy_succ + policy_fail),
        "crawl_success_rate": _rate(crawl_succ, crawl_succ + crawl_fail),
        "parser_success_rate": _rate(parse_succ, parse_succ + parse_fail),
        "pattern_approval_rate": _rate(approved, built),
        "original_reuse_block_rate": _rate(reuse_blocked, processed),
        "pii_block_rate": _rate(pii_blocked, processed),
        "duplicate_rate": _rate(duplicate, processed),
        "license_manual_review_rate": _rate(license_manual, license_total),
    }


def compute_slo(m: InMemoryMetrics) -> dict[str, dict]:
    """각 SLO 타이머의 {p95, target_ms, breached}. breached = timings 존재 & p95 > target."""
    result: dict[str, dict] = {}
    for name, target_ms in SLO_TARGETS_MS.items():
        p95 = m.percentile(name, 95.0)
        breached = bool(m.timings(name)) and p95 > target_ms
        result[name] = {"p95": p95, "target_ms": target_ms, "breached": breached}
    return result


@dataclass
class Alert:
    name: str
    value: float
    threshold: float
    fired: bool


DEFAULT_ALERT_THRESHOLDS: dict[str, float] = {
    "crawl_error_rate": 0.10,            # >10%
    "pii_detected_high": 0.20,           # pii_block_rate > 20%
    "reuse_risk_high_rate": 0.30,        # reuse_block_rate > 30%
}


def evaluate_alerts(m: InMemoryMetrics, *, thresholds: dict[str, float] | None = None) -> list[Alert]:
    """§12.3 알람 평가. fired 여부 무관 전체 Alert 리스트 반환."""
    if thresholds is None:
        thresholds = DEFAULT_ALERT_THRESHOLDS

    crawl_fail = m.counter(M_CRAWL_FAILED)
    crawl_succ = m.counter(M_CRAWL_SUCCESS)
    pii_blocked = m.counter(M_PII_BLOCKED)
    reuse_blocked = m.counter(M_REUSE_BLOCKED)
    processed = m.counter(M_PROCESSED)

    crawl_error_rate = _rate(crawl_fail, crawl_fail + crawl_succ)
    pii_rate = _rate(pii_blocked, processed)
    reuse_rate = _rate(reuse_blocked, processed)

    return [
        Alert("crawl_error_rate", crawl_error_rate, thresholds.get("crawl_error_rate", 0.10),
              crawl_error_rate > thresholds.get("crawl_error_rate", 0.10)),
        Alert("pii_detected_high", pii_rate, thresholds.get("pii_detected_high", 0.20),
              pii_rate > thresholds.get("pii_detected_high", 0.20)),
        Alert("reuse_risk_high_rate", reuse_rate, thresholds.get("reuse_risk_high_rate", 0.30),
              reuse_rate > thresholds.get("reuse_risk_high_rate", 0.30)),
    ]