"""Pattern 파이프라인 (설계서 §9 + Reuse Risk Score 스파이크).

UPE 핵심 IP — 원본 재사용 위험 점수 산정 / 추상화 가드 / 역복원 테스트 / 상태기계.

순수 모듈(외부 의존 없음). 실 검출기(logo/face/artwork)는 boolean 입력 또는
어댑터 인터페이스로 받는다 — 크로스레포 import 금지(self-contained).
"""
from __future__ import annotations

from app.pattern.abstraction_guard import (
    ABSTRACTION_ALLOWED,
    REMOVAL_TARGETS,
    guard,
)
from app.pattern.pattern_state import (
    PATTERN_TRANSITIONS,
    can_transition,
    decide_pattern_status,
)
from app.pattern.reconstruction_test import (
    RECON_SIMILARITY_THRESHOLD,
    estimate_reconstructability,
    run_reconstruction_test,
)
from app.pattern.reuse_risk import (
    GRADE_THRESHOLDS,
    HARDRULE_TEXT_OVERLAP,
    WEIGHTS,
    compute_reuse_risk,
    score_to_risk,
)

__all__ = [
    # reuse_risk
    "compute_reuse_risk",
    "score_to_risk",
    "WEIGHTS",
    "GRADE_THRESHOLDS",
    "HARDRULE_TEXT_OVERLAP",
    # abstraction_guard
    "guard",
    "REMOVAL_TARGETS",
    "ABSTRACTION_ALLOWED",
    # reconstruction_test
    "run_reconstruction_test",
    "estimate_reconstructability",
    "RECON_SIMILARITY_THRESHOLD",
    # pattern_state
    "decide_pattern_status",
    "can_transition",
    "PATTERN_TRANSITIONS",
]
