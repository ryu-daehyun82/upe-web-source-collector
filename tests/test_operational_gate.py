from types import SimpleNamespace

from app.operational_gate import evaluate_operational, evaluate_pattern
from app.models.enums import PatternStatus, ReuseRisk, LicenseStatus, PiiStatus, SourceStatus


def _ok_kwargs(**over):
    base = dict(
        source_status="parsed",
        license_status="allowed",
        pii_status="clean",
        original_reuse_risk="low",
        pattern_status="approved",
        recon_test_passed=True,
    )
    base.update(over)
    return base


class TestEvaluateOperational:
    def test_all_pass(self):
        result = evaluate_operational(**_ok_kwargs())
        assert result.operational is True
        assert result.reasons == []

    def test_reuse_high_fails(self):
        result = evaluate_operational(**_ok_kwargs(original_reuse_risk="high"))
        assert result.operational is False
        assert "reuse_risk" in result.reasons

    def test_reuse_blocked_fails(self):
        result = evaluate_operational(**_ok_kwargs(original_reuse_risk="blocked"))
        assert result.operational is False
        assert "reuse_risk" in result.reasons

    def test_pattern_not_approved(self):
        result = evaluate_operational(**_ok_kwargs(pattern_status="blocked"))
        assert result.operational is False
        assert "pattern_status" in result.reasons

    def test_recon_none_fails(self):
        result = evaluate_operational(**_ok_kwargs(recon_test_passed=None))
        assert result.operational is False
        assert "recon_test" in result.reasons

    def test_recon_false_fails(self):
        result = evaluate_operational(**_ok_kwargs(recon_test_passed=False))
        assert result.operational is False
        assert "recon_test" in result.reasons

    def test_license_unknown_fails(self):
        result = evaluate_operational(**_ok_kwargs(license_status="unknown"))
        assert result.operational is False
        assert "license_status" in result.reasons

    def test_license_conditional_approved_ok(self):
        result = evaluate_operational(**_ok_kwargs(license_status="conditional_approved"))
        assert result.operational is True
        assert result.reasons == []

    def test_source_discovered_fails(self):
        result = evaluate_operational(**_ok_kwargs(source_status="discovered"))
        assert result.operational is False
        assert "source_status" in result.reasons

    def test_pii_sensitive_fails(self):
        result = evaluate_operational(**_ok_kwargs(pii_status="sensitive"))
        assert result.operational is False
        assert "pii_status" in result.reasons

    def test_pii_redacted_ok(self):
        result = evaluate_operational(**_ok_kwargs(pii_status="redacted"))
        assert result.operational is True
        assert result.reasons == []

    def test_multiple_failures(self):
        result = evaluate_operational(**_ok_kwargs(source_status="discovered", license_status="unknown", recon_test_passed=False))
        assert result.operational is False
        assert {"source_status", "license_status", "recon_test"} <= set(result.reasons)

    def test_enum_inputs(self):
        result = evaluate_operational(
            source_status=SourceStatus.parsed,
            license_status=LicenseStatus.allowed,
            pii_status=PiiStatus.clean,
            original_reuse_risk=ReuseRisk.low,
            pattern_status=PatternStatus.approved,
            recon_test_passed=True,
        )
        assert result.operational is True


class TestEvaluatePattern:
    def test_evaluate_pattern_pass(self):
        source = SimpleNamespace(crawl_status="parsed")
        pattern = SimpleNamespace(
            license_status="allowed",
            pii_status="clean",
            original_reuse_risk="low",
            pattern_status="approved",
            recon_test_passed=True,
        )
        result = evaluate_pattern(pattern, source)
        assert result.operational is True

    def test_evaluate_pattern_fail(self):
        source = SimpleNamespace(crawl_status="discovered")
        pattern = SimpleNamespace(
            license_status="allowed",
            pii_status="clean",
            original_reuse_risk="low",
            pattern_status="approved",
            recon_test_passed=True,
        )
        result = evaluate_pattern(pattern, source)
        assert result.operational is False
        assert "source_status" in result.reasons