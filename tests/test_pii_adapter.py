from app.models.enums import PiiStatus
from app.adapters.pii import (
    PiiFinding, PiiScanResult, FallbackPiiScanner, HarvesterPiiScanner,
    get_pii_scanner, _mask,
)


def test_clean_text():
    scanner = FallbackPiiScanner()
    result = scanner.scan("오늘 날씨가 좋습니다 회의는 3시")
    assert result.has_pii() is False
    assert result.status == PiiStatus.clean
    assert result.findings == []


def test_empty_and_none():
    scanner = FallbackPiiScanner()
    result_empty = scanner.scan("")
    assert result_empty.has_pii() is False
    assert result_empty.status == PiiStatus.clean

    result_none = scanner.scan(None)
    assert result_none.has_pii() is False
    assert result_none.status == PiiStatus.clean


def test_email_redacted():
    scanner = FallbackPiiScanner()
    result = scanner.scan("문의: hong@example.com 으로")
    assert result.types() == {"email"}
    assert result.status == PiiStatus.redacted
    for finding in result.findings:
        if finding.pii_type == "email":
            assert "@example" not in finding.masked


def test_rrn_sensitive():
    scanner = FallbackPiiScanner()
    result = scanner.scan("주민번호 901201-1234567 입니다")
    assert "rrn" in result.types()
    assert result.status == PiiStatus.sensitive


def test_brn_sensitive():
    scanner = FallbackPiiScanner()
    result = scanner.scan("사업자 123-45-67890")
    assert "brn" in result.types()
    assert result.status == PiiStatus.sensitive


def test_phone_redacted():
    scanner = FallbackPiiScanner()
    result = scanner.scan("연락처 010-1234-5678")
    assert "phone_kr" in result.types()
    assert result.status == PiiStatus.redacted


def test_credit_card_sensitive():
    scanner = FallbackPiiScanner()
    result = scanner.scan("카드 1234-5678-9012-3456")
    assert "credit_card" in result.types()
    assert result.status == PiiStatus.sensitive


def test_mixed_high_risk_wins():
    scanner = FallbackPiiScanner()
    result = scanner.scan("메일 a@b.com 주민 901201-1234567")
    types = result.types()
    assert "email" in types
    assert "rrn" in types
    assert result.status == PiiStatus.sensitive


def test_mask_helper():
    m = _mask("hong@example.com")
    assert m[:2] == "ho"
    assert set(m[2:]) == {"*"}
    assert _mask("a") == "*"
    assert _mask("ab") == "**"


def test_get_scanner_fallback():
    s = get_pii_scanner()
    assert s.name == "fallback"
    assert isinstance(s, FallbackPiiScanner)


def test_harvester_wrapper_delegates():
    scanner = HarvesterPiiScanner(impl=object())
    result = scanner.scan("hong@example.com")
    assert any(f.pii_type == "email" for f in result.findings)
    assert result.status == PiiStatus.redacted