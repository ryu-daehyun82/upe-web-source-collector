"""logo/unique_artwork 검출기 어댑터+폴백 (§13.4 / §9.2 스파이크).

실 이미지 ML(makeup-ai insightface 등)은 외부 import 위임, 부재 시 feature
메타데이터 휴리스틱 폴백(현재 폴백 활성). 판정 결과는 reuse_risk 가 쓰는
logo_detected / unique_artwork_detected boolean 으로 detect_visual_flags 를 통해 연결.

판정 우선순위(공통): 1) 명시 boolean 플래그 > 2) 명시 score(임계) > 3) 휴리스틱 > 4) 기본 False.
bool 은 int 하위타입이므로 score 검사 전에 isinstance(x, bool) 로 배제.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

LOGO_SCORE_THRESHOLD = 0.5
ARTWORK_SCORE_THRESHOLD = 0.5
ENTROPY_HIGH = 7.0          # 0~8 스케일, 이 이상이면 고엔트로피(고유작품 후보)
STOCK_MATCH_LOW = 0.2       # 이하면 스톡 매칭 약함(고유작품 후보)

# 외부 검출기 연결지점(현재 미존재 → ImportError 폴백):
#   logo: 신규(템플릿매칭+소형 분류기), artwork: 신규 분류기. (face 는 makeup-ai identity_guard 별도)
_LOGO_MODULE = "upe_vision.logo_detector"
_ARTWORK_MODULE = "upe_vision.artwork_detector"


@dataclass(frozen=True)
class DetectionResult:
    """시각 요소 검출 결과."""
    detected: bool
    confidence: float       # [0,1]
    detector: str           # "fallback" | "external"
    reason: str | None      # 판정 근거


class FallbackLogoDetector:
    """로고 검출 폴백: 명시 플래그 → score → 휴리스틱 → 기본 False."""
    name = "fallback"

    def detect(self, feature: dict) -> DetectionResult:
        # 1) 명시 boolean 플래그
        v = feature.get("logo_detected")
        if isinstance(v, bool):
            return DetectionResult(
                detected=v, confidence=1.0 if v else 0.0,
                detector=self.name, reason="explicit_flag",
            )
        # 2) score 기반
        score = feature.get("logo_score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            detected = score >= LOGO_SCORE_THRESHOLD
            return DetectionResult(
                detected=detected, confidence=float(score),
                detector=self.name, reason="score",
            )
        # 3) 휴리스틱
        has_alpha = feature.get("has_alpha_channel", False)
        regions = feature.get("high_contrast_region_count", 0)
        vector = feature.get("vector_graphic", False)
        if vector or (has_alpha and regions >= 1):
            return DetectionResult(
                detected=True, confidence=0.6,
                detector=self.name, reason="heuristic",
            )
        # 4) 기본 False
        return DetectionResult(detected=False, confidence=0.0, detector=self.name, reason=None)


class FallbackArtworkDetector:
    """고유 작품 검출 폴백: 명시 플래그 → score → 엔트로피 휴리스틱 → 기본 False."""
    name = "fallback"

    def detect(self, feature: dict) -> DetectionResult:
        # 1) 명시 boolean 플래그
        v = feature.get("unique_artwork_detected")
        if isinstance(v, bool):
            return DetectionResult(
                detected=v, confidence=1.0 if v else 0.0,
                detector=self.name, reason="explicit_flag",
            )
        # 2) score 기반
        score = feature.get("artwork_score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            detected = score >= ARTWORK_SCORE_THRESHOLD
            return DetectionResult(
                detected=detected, confidence=float(score),
                detector=self.name, reason="score",
            )
        # 3) 엔트로피 휴리스틱(고엔트로피 + 약한 스톡매칭 → 고유작품 후보)
        entropy = feature.get("image_entropy")
        stock = feature.get("stock_match_score")
        entropy_ok = (
            isinstance(entropy, (int, float))
            and not isinstance(entropy, bool)
            and entropy >= ENTROPY_HIGH
        )
        stock_ok = (stock is None) or (
            isinstance(stock, (int, float))
            and not isinstance(stock, bool)
            and stock <= STOCK_MATCH_LOW
        )
        if entropy_ok and stock_ok:
            return DetectionResult(
                detected=True, confidence=0.7,
                detector=self.name, reason="heuristic_entropy",
            )
        # 4) 기본 False
        return DetectionResult(detected=False, confidence=0.0, detector=self.name, reason=None)


class ExternalDetector:
    """외부 검출기 래퍼. 본 파일럿은 결과 매핑 불명확 → 폴백에 위임(추후 교체 자리)."""
    name = "external"

    def __init__(self, impl: Any, fallback: Any) -> None:
        self.impl = impl
        self.fallback = fallback

    def detect(self, feature: dict) -> DetectionResult:
        return self.fallback.detect(feature)


def _try_import(module_name: str) -> Any:
    """모듈 import 시도. 실패(ImportError) 시 None."""
    try:
        return importlib.import_module(module_name)
    except ImportError:
        return None


def get_logo_detector() -> Any:
    """로고 검출기 팩토리: 외부 모듈 우선, 없으면 폴백."""
    mod = _try_import(_LOGO_MODULE)
    return ExternalDetector(mod, FallbackLogoDetector()) if mod else FallbackLogoDetector()


def get_artwork_detector() -> Any:
    """작품 검출기 팩토리: 외부 모듈 우선, 없으면 폴백."""
    mod = _try_import(_ARTWORK_MODULE)
    return ExternalDetector(mod, FallbackArtworkDetector()) if mod else FallbackArtworkDetector()


def detect_visual_flags(feature: dict) -> dict:
    """logo/artwork 검출 → {"logo_detected", "unique_artwork_detected"} bool dict.

    compute_reuse_risk 전에 feature 를 enrich 하는 용도(하드룰 입력).
    """
    logo = get_logo_detector().detect(feature)
    art = get_artwork_detector().detect(feature)
    return {"logo_detected": logo.detected, "unique_artwork_detected": art.detected}