"""Reconstruction Test 하니스 (스파이크 §5 / v2.1 P-9 릴리즈게이트 G4).

핵심 원칙: "점수 낮음"이 아니라 **"실제로 원본 복원 불가"**를 증명한다.

이상적으로는 패턴(feature)만으로 생성엔진을 돌려 산출물을 만들고, 원본과
perceptual hash / layout IoU / text overlap 을 비교한다. 실 생성엔진이 아직
없으므로, **feature 기반 복원가능성(reconstructability) 추정 함수**로 구현한다.

복원가능성이 임계를 초과하면 = 추상화 부족 = 테스트 실패(recon_test_passed=False).
릴리즈게이트 G4 에서 이 결과로 release 차단.

(b)안 결합 구조:
  - **텍스트 = 하드룰 전용**: text_overlap >= RECON_TEXT_LEAK_HARD 이면 시각신호와
    무관하게 즉시 실패(fail_reason="text_leak"). 가중합에는 포함하지 않는다.
  - **시각신호 = 연속 복원도**: layout/color/structure 만으로 [0,1] 정규화
    (가중치 합 1.0). 텍스트 누출이 없어도 layout+color+structure 가 원본에
    가까운 브랜드 클론을 G4 에서 차단할 수 있다.

순수 모듈(외부 의존 없음). 생성엔진 연결지점은 주석으로 표시.
"""
from __future__ import annotations

from app.pattern.reuse_risk import (
    color_signature,
    layout_similarity,
    structure_fingerprint,
    text_overlap,
)

# ----------------------------------------------------------------------------
# 보정 가능한 상수 (스파이크 §5 — golden brand-clone 복원 시 release block)
# ----------------------------------------------------------------------------

#: 복원 유사도 임계. 이 값 초과면 "복원 가능"으로 보고 테스트 실패.
RECON_SIMILARITY_THRESHOLD: float = 0.60

#: reconstructability 추정 가중치 — 시각신호만(합 1.0). 텍스트는 하드룰 전용이라 제외.
RECON_WEIGHTS: dict[str, float] = {
    "layout_similarity": 0.50,      # 레이아웃 완전성(가장 강한 복원 단서)
    "color_signature": 0.30,        # 색 팔레트 재현
    "structure_fingerprint": 0.20,  # 섹션/영역 구조 근접
}

#: 원문 잔존 하드 실패 임계 — 이 이상이면 다른 신호와 무관하게 즉시 실패(text_leak).
RECON_TEXT_LEAK_HARD: float = 0.15


def estimate_reconstructability(feature: dict) -> float:
    """패턴 feature 의 **시각신호만으로** 원본 복원가능성 [0,1] 추정.

    layout/color/structure 완전성을 가중 결합(합 1.0). 높을수록 패턴만으로
    원본에 가깝게 되살릴 수 있음(= 추상화 부족). 텍스트 누출은 가중합이 아니라
    run_reconstruction_test 의 하드룰로 별도 처리한다.

    생성엔진 연결지점(추후):
      reconstruct(feature) -> artifact
      compare(artifact, original) -> {phash_sim, layout_iou, text_overlap}
      이 비교 결과로 아래 추정을 실측값으로 대체.
    """
    layout = layout_similarity(feature)
    color = color_signature(feature)
    struct = structure_fingerprint(feature)

    recon = (
        RECON_WEIGHTS["layout_similarity"] * layout
        + RECON_WEIGHTS["color_signature"] * color
        + RECON_WEIGHTS["structure_fingerprint"] * struct
    )
    if recon < 0.0:
        recon = 0.0
    elif recon > 1.0:
        recon = 1.0
    return round(recon, 6)


def run_reconstruction_test(
    feature: dict,
    *,
    threshold: float = RECON_SIMILARITY_THRESHOLD,
) -> dict:
    """역복원 불가 증명 테스트 실행(릴리즈게이트 G4).

    처리 순서:
      1) 텍스트 하드룰 — text_overlap >= RECON_TEXT_LEAK_HARD 이면 시각신호와
         무관하게 즉시 실패(fail_reason="text_leak").
      2) 시각신호 연속 복원도 — recon > threshold 이면 실패
         (fail_reason="high_reconstructability").

    Returns:
        {
          "recon_test_passed": bool,        # WebPattern.recon_test_passed 계약
          "reconstructability": float[0,1],
          "threshold": float,
          "fail_reason": str | None,        # "text_leak" | "high_reconstructability" | None
        }
    passed=True 면 "복원 불가 증명" 성공(추상화 충분).
    """
    # 텍스트 하드룰: 원문 잔존이 누출 임계 이상이면 복원 직접 가능 → 즉시 실패.
    probe = feature.get("_leak_probe") if isinstance(feature.get("_leak_probe"), dict) else {}
    orig_text = feature.get("original_text") or probe.get("original_text")
    pat_text = feature.get("pattern_text") or probe.get("pattern_text")
    t_overlap = text_overlap(orig_text, pat_text)

    if t_overlap >= RECON_TEXT_LEAK_HARD:
        return {
            "recon_test_passed": False,
            "reconstructability": estimate_reconstructability(feature),
            "threshold": threshold,
            "fail_reason": "text_leak",
        }

    # 시각신호 기반 연속 복원도 평가.
    recon = estimate_reconstructability(feature)
    passed = recon <= threshold
    return {
        "recon_test_passed": passed,
        "reconstructability": recon,
        "threshold": threshold,
        "fail_reason": None if passed else "high_reconstructability",
    }