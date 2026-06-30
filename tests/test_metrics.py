from app.metrics import (
    InMemoryMetrics, NullMetrics, timer,
    compute_kpis, compute_slo, evaluate_alerts,
    SLO_TARGETS_MS, DEFAULT_ALERT_THRESHOLDS,
    M_CRAWL_SUCCESS, M_CRAWL_FAILED, M_PATTERN_BUILT, M_PATTERN_APPROVED,
    M_PROCESSED, M_DUPLICATE, M_PII_BLOCKED, M_REUSE_BLOCKED,
    M_POLICY_CHECK_SUCCESS, M_POLICY_CHECK_FAILED, M_PARSE_SUCCESS, M_PARSE_FAILED,
    M_LICENSE_MANUAL_REVIEW, M_LICENSE_TOTAL,
    T_STATIC_FETCH, T_PDF_PARSE,
)


def test_inc_and_counter():
    m = InMemoryMetrics()
    m.inc("x")
    m.inc("x", 2)
    assert m.counter("x") == 3.0
    assert m.counter("none") == 0.0


def test_observe_timings():
    m = InMemoryMetrics()
    m.observe("t", 10)
    m.observe("t", 20)
    assert m.timings("t") == [10, 20]


def test_percentile():
    m = InMemoryMetrics()
    for v in [10, 20, 30, 40, 50]:
        m.observe("t", v)
    assert m.percentile("t", 95) == 50
    assert m.percentile("t", 50) == 30
    assert InMemoryMetrics().percentile("empty", 95) == 0.0


def test_reset():
    m = InMemoryMetrics()
    m.inc("x")
    m.observe("t", 1)
    m.reset()
    assert m.counter("x") == 0.0
    assert m.timings("t") == []


def test_kpis_rates():
    m = InMemoryMetrics()
    m.inc(M_CRAWL_SUCCESS, 9)
    m.inc(M_CRAWL_FAILED, 1)
    m.inc(M_PATTERN_BUILT, 10)
    m.inc(M_PATTERN_APPROVED, 8)
    m.inc(M_PROCESSED, 100)
    m.inc(M_DUPLICATE, 5)
    m.inc(M_POLICY_CHECK_SUCCESS, 99)
    m.inc(M_POLICY_CHECK_FAILED, 1)
    m.inc(M_PARSE_SUCCESS, 19)
    m.inc(M_PARSE_FAILED, 1)
    m.inc(M_PII_BLOCKED, 2)
    m.inc(M_REUSE_BLOCKED, 3)
    m.inc(M_LICENSE_MANUAL_REVIEW, 4)
    m.inc(M_LICENSE_TOTAL, 20)
    k = compute_kpis(m)
    assert k["crawl_success_rate"] == 0.9
    assert k["pattern_approval_rate"] == 0.8
    assert k["duplicate_rate"] == 0.05
    assert abs(k["policy_check_success_rate"] - 0.99) < 1e-9
    assert k["parser_success_rate"] == 0.95
    assert k["pii_block_rate"] == 0.02
    assert k["original_reuse_block_rate"] == 0.03
    assert k["license_manual_review_rate"] == 0.2


def test_kpis_zero_denominator():
    k = compute_kpis(InMemoryMetrics())
    assert k["crawl_success_rate"] == 0.0
    assert k["pattern_approval_rate"] == 0.0
    assert k["duplicate_rate"] == 0.0
    assert k["policy_check_success_rate"] == 0.0
    assert k["parser_success_rate"] == 0.0
    assert k["pii_block_rate"] == 0.0
    assert k["original_reuse_block_rate"] == 0.0
    assert k["license_manual_review_rate"] == 0.0


def test_slo_breached():
    m = InMemoryMetrics()
    for v in [10000, 20000, 40000]:
        m.observe(T_STATIC_FETCH, v)
    slo = compute_slo(m)
    assert slo[T_STATIC_FETCH]["p95"] == 40000
    assert slo[T_STATIC_FETCH]["target_ms"] == 30000
    assert slo[T_STATIC_FETCH]["breached"] is True
    assert slo[T_PDF_PARSE]["breached"] is False
    assert slo[T_PDF_PARSE]["p95"] == 0.0


def test_alerts_crawl_error_fires():
    m = InMemoryMetrics()
    m.inc(M_CRAWL_SUCCESS, 8)
    m.inc(M_CRAWL_FAILED, 2)
    m.inc(M_PROCESSED, 100)
    m.inc(M_REUSE_BLOCKED, 5)
    m.inc(M_PII_BLOCKED, 1)
    alerts = evaluate_alerts(m)
    assert len(alerts) == 3
    by = {a.name: a for a in alerts}
    assert by["crawl_error_rate"].fired is True
    assert abs(by["crawl_error_rate"].value - 0.2) < 1e-9
    assert by["reuse_risk_high_rate"].fired is False
    assert by["pii_detected_high"].fired is False


def test_alerts_custom_threshold():
    m = InMemoryMetrics()
    m.inc(M_CRAWL_SUCCESS, 9)
    m.inc(M_CRAWL_FAILED, 1)
    alerts = evaluate_alerts(m, thresholds={
        "crawl_error_rate": 0.05,
        "pii_detected_high": 0.2,
        "reuse_risk_high_rate": 0.3,
    })
    by = {a.name: a for a in alerts}
    assert by["crawl_error_rate"].fired is True


def test_null_metrics_noop():
    n = NullMetrics()
    n.inc("x")
    n.observe("t", 5)


def test_timer_records():
    m = InMemoryMetrics()
    with timer(m, "op"):
        pass
    assert len(m.timings("op")) == 1
    assert m.timings("op")[0] >= 0.0


def test_slo_targets_keys():
    assert T_STATIC_FETCH in SLO_TARGETS_MS
    assert SLO_TARGETS_MS[T_STATIC_FETCH] == 30000
    assert "crawl_error_rate" in DEFAULT_ALERT_THRESHOLDS