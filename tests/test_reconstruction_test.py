"""Reconstruction Test 하니스 테스트 (스파이크 §5 / G4).

복원가능성 추정 + 실패 케이스(추상화 부족) + 원문 잔존 하드 실패.
"""
import pytest

from app.pattern.reconstruction_test import (
    RECON_SIMILARITY_THRESHOLD,
    estimate_reconstructability,
    run_reconstruction_test,
)


def test_low_signal_passes():
    feat = {"layout_similarity": 0.1, "color_signature": 0.1, "structure_fingerprint": 0.1}
    out = run_reconstruction_test(feat)
    assert out["recon_test_passed"] is True
    assert out["reconstructability"] <= RECON_SIMILARITY_THRESHOLD
    assert out["fail_reason"] is None


def test_high_layout_color_fails():
    # 레이아웃·색·구조 모두 높음 → 복원 가능 → 실패(추상화 부족).
    feat = {"layout_similarity": 0.95, "color_signature": 0.95,
            "structure_fingerprint": 0.95}
    out = run_reconstruction_test(feat)
    assert out["recon_test_passed"] is False
    assert out["fail_reason"] == "high_reconstructability"


def test_text_leak_hard_fail():
    feat = {
        "original_text": "this is the exact original copy that should be gone",
        "pattern_text": "this is the exact original copy that should be gone",
        "layout_similarity": 0.0,
    }
    out = run_reconstruction_test(feat)
    assert out["recon_test_passed"] is False
    assert out["fail_reason"] == "text_leak"


def test_leak_probe_input_honored():
    # guard 가 분리한 _leak_probe 로도 누출 탐지.
    feat = {
        "_leak_probe": {
            "original_text": "secret original phrasing that must not be reconstructable here",
            "pattern_text": "secret original phrasing that must not be reconstructable here",
        }
    }
    out = run_reconstruction_test(feat)
    assert out["recon_test_passed"] is False
    assert out["fail_reason"] == "text_leak"


def test_estimate_monotonic():
    low = estimate_reconstructability({"layout_similarity": 0.1})
    high = estimate_reconstructability({"layout_similarity": 0.9})
    assert high > low


def test_custom_threshold():
    feat = {"layout_similarity": 0.7, "color_signature": 0.0, "structure_fingerprint": 0.0}
    # recon = 0.25*0.7 = 0.175
    out_strict = run_reconstruction_test(feat, threshold=0.1)
    out_loose = run_reconstruction_test(feat, threshold=0.9)
    assert out_strict["recon_test_passed"] is False
    assert out_loose["recon_test_passed"] is True


def test_result_contract_keys():
    out = run_reconstruction_test({"layout_similarity": 0.2})
    assert set(out.keys()) == {
        "recon_test_passed", "reconstructability", "threshold", "fail_reason"
    }
    assert isinstance(out["recon_test_passed"], bool)
