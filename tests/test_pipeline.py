from app.pipeline import run_pattern_governance, run_batch, GovernanceDecision
from app.models.enums import PatternStatus, ReuseRisk, PiiStatus


def test_clean_pattern_approved():
    raw = {
        "layout_similarity": 0.1,
        "color_signature": 0.1,
        "structure_fingerprint": 0.1,
    }
    d = run_pattern_governance(raw)
    assert d.operational is True
    assert d.pattern_status == PatternStatus.approved.value
    assert d.blocked_reason is None
    assert d.original_reuse_risk == ReuseRisk.low.value
    assert d.recon_test_passed is True
    assert d.pii_status == PiiStatus.clean.value


def test_text_leak_blocked():
    txt = "this is the exact original sentence that must not leak into the pattern at all"
    raw = {
        "original_text": txt,
        "pattern_text": txt,
        "layout_similarity": 0.0,
    }
    d = run_pattern_governance(raw)
    assert d.operational is False
    assert d.original_reuse_risk == ReuseRisk.blocked.value
    assert d.blocked_reason == "reuse_blocked"
    assert d.reuse_subscores["text_overlap"] > 0


def test_pii_sensitive_blocked():
    raw = {
        "layout_similarity": 0.1,
        "color_signature": 0.1,
        "structure_fingerprint": 0.1,
    }
    d = run_pattern_governance(raw, pii_text="주민번호 901201-1234567 임")
    assert d.operational is False
    assert d.blocked_reason == "pii_sensitive"
    assert d.pii_status == PiiStatus.sensitive.value
    assert "rrn" in d.pii_types


def test_pii_takes_priority_over_reuse():
    txt = "exact original copy that leaks fully into pattern text here now"
    raw = {
        "original_text": txt,
        "pattern_text": txt,
    }
    d = run_pattern_governance(raw, pii_text="010-1234-5678 전화 901201-1234567 주민")
    assert d.blocked_reason == "pii_sensitive"


def test_logo_high_blocked():
    raw = {
        "layout_similarity": 0.1,
        "color_signature": 0.1,
        "structure_fingerprint": 0.1,
        "logo_score": 0.9,
    }
    d = run_pattern_governance(raw)
    assert d.operational is False
    assert d.original_reuse_risk == ReuseRisk.high.value
    assert d.blocked_reason == "reuse_high"
    assert d.reuse_subscores["logo"] is True


def test_high_visual_signals_blocked():
    raw = {
        "layout_similarity": 0.95,
        "color_signature": 0.95,
        "structure_fingerprint": 0.95,
    }
    d = run_pattern_governance(raw)
    assert d.operational is False
    assert d.blocked_reason in {"reuse_high", "reuse_blocked", "recon_failed"}


def test_brand_risk_lookup_injected():
    lookup = lambda domain: 1.0 if domain == "brandy.com" else None
    raw = {
        "layout_similarity": 0.4,
        "color_signature": 0.4,
        "structure_fingerprint": 0.4,
        "domain": "brandy.com",
    }
    d_high = run_pattern_governance(raw, brand_risk_lookup=lookup)
    raw2 = dict(raw)
    raw2["domain"] = "neutral.com"
    d_low = run_pattern_governance(raw2, brand_risk_lookup=lookup)
    assert d_high.reuse_score > d_low.reuse_score


def test_abstracted_feature_strips_original():
    raw = {
        "layout_similarity": 0.1,
        "raw_text": "long original body text",
        "section_order": [1, 2, 3],
    }
    d = run_pattern_governance(raw)
    assert "raw_text" not in d.abstracted_feature
    assert "section_order" in d.abstracted_feature
    assert len(d.removed_items) >= 1


def test_run_batch():
    feats = [
        {
            "layout_similarity": 0.1,
            "color_signature": 0.1,
            "structure_fingerprint": 0.1,
        },
        {
            "layout_similarity": 0.95,
            "color_signature": 0.95,
            "structure_fingerprint": 0.95,
        },
    ]
    results = run_batch(feats)
    assert len(results) == 2
    assert results[0].operational is True
    assert results[1].operational is False
    assert all(isinstance(r, GovernanceDecision) for r in results)