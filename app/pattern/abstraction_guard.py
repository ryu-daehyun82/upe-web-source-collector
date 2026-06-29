"""Pattern Abstraction Guard (설계서 §9.2 / §9.3).

패턴 feature 에서 **제거 대상**(저작권 위험 표현)을 솎아내고 **추상화 허용 대상**만
통과시키는 필터. Risk Score 산정 전에 실행되어, 애초에 원본 표현이 패턴에 실려
들어가지 않도록 하는 1차 방어선.

순수 모듈(외부 의존 없음). raw_text / image_pixels 가 남아 있으면 제거하고
removed_items 에 기록 + 플래그(raw_text_removed / image_pixels_removed) 로 보고.
"""
from __future__ import annotations

from collections.abc import Iterable

# ----------------------------------------------------------------------------
# §9.2 제거 대상 — 패턴에 절대 남으면 안 되는 원본 표현(저작권 위험)
# ----------------------------------------------------------------------------
#: feature key -> 사람이 읽는 제거 사유.
REMOVAL_TARGETS: dict[str, str] = {
    "raw_text": "long_form_original_text",          # 장문 원문
    "original_text": "long_form_original_text",      # 원문(누출 비교용은 별도 보존)
    "verbatim_text": "verbatim_phrases",             # 고유 문구(축자 인용)
    "unique_phrases": "unique_phrases",
    "image_pixels": "image_pixels",                  # 이미지 픽셀(로고/인물/일러스트 원본)
    "raw_image": "image_pixels",
    "logo_asset": "logo",                            # 로고
    "face_crops": "person_face",                     # 인물
    "person_images": "person_face",
    "signature": "signature",                        # 서명
    "unique_artwork": "original_illustration",       # 독창 일러스트
    "illustration_asset": "original_illustration",
    "design_replica": "design_replica",              # 디자인 복제(픽셀퍼펙트)
}

# ----------------------------------------------------------------------------
# §9.3 추상화 허용 대상 — 통과시켜도 되는 구조적 메타데이터(표현 아님)
# ----------------------------------------------------------------------------
#: 패턴에 남겨도 되는 키 화이트리스트.
ABSTRACTION_ALLOWED: frozenset[str] = frozenset(
    {
        "section_order",        # 섹션 순서
        "layout_type",          # 레이아웃 유형
        "region_ratios",        # 영역 비율
        "color_count",          # 색 개수
        "table_structure",      # 표 구조(행/열 수 등)
        "card_count",           # 카드 수
        "slide_flow",           # 슬라이드 흐름
        # 점수화에 쓰이는 추상 시그니처(원본 표현 아님) — 통과.
        "layout_similarity",
        "color_signature",
        "structure_fingerprint",
        "region_iou",
        "original_layout_tree",
        "pattern_layout_tree",
        "original_color_hist",
        "pattern_color_hist",
        "original_structure_vec",
        "pattern_structure_vec",
        # 정책/메타 — 점수 입력으로 필요(표현 아님).
        "brand_risk",
        "domain",
        # 하드룰용 boolean 플래그(검출 결과만, 원본 자산 아님) — 통과.
        "logo_detected",
        "face_detected",
        "unique_artwork_detected",
    }
)

#: 원문 누출 탐지(text_overlap)를 위해 비교 입력으로만 보존이 허용되는 키.
#: feature 본문에는 남기지 않되, guard 가 반환하는 비교 컨텍스트로 분리한다.
_TEXT_LEAK_PROBE_KEYS: frozenset[str] = frozenset({"original_text", "pattern_text"})


def guard(feature: dict) -> tuple[dict, list[str]]:
    """feature 에서 제거 대상을 솎아내고 추상화 허용 대상만 통과.

    Args:
        feature: 패턴 빌더가 만든 원시 feature dict.

    Returns:
        (abstracted_feature, removed_items)
          - abstracted_feature: 허용 대상만 남은 dict. + 보고 플래그:
              raw_text_removed / image_pixels_removed (bool),
              removed_items (list[str]).
            text_overlap 누출검사용 original_text/pattern_text 는 점수 파이프라인이
            쓸 수 있도록 _leak_probe 하위 dict 로 분리 보존(본문에는 미노출).
          - removed_items: 제거된 키 목록(사유 라벨).
    """
    abstracted: dict = {}
    removed: list[str] = []
    leak_probe: dict = {}
    raw_text_removed = False
    image_pixels_removed = False

    for key, value in feature.items():
        # 1) 명시적 제거 대상
        if key in REMOVAL_TARGETS:
            reason = REMOVAL_TARGETS[key]
            removed.append(reason)
            if reason in ("long_form_original_text", "verbatim_phrases", "unique_phrases"):
                raw_text_removed = True
            if reason in ("image_pixels", "logo", "person_face", "original_illustration", "design_replica"):
                image_pixels_removed = True
            # original_text 는 누출검사 입력으로만 분리 보존(본문 미노출).
            if key == "original_text":
                leak_probe["original_text"] = value
            continue

        # 2) 누출검사 probe(원문 비교 텍스트) — 본문엔 안 남기고 probe 로 분리.
        if key in _TEXT_LEAK_PROBE_KEYS:
            leak_probe[key] = value
            continue

        # 3) 추상화 허용 화이트리스트만 통과
        if key in ABSTRACTION_ALLOWED:
            abstracted[key] = value
            continue

        # 4) 화이트리스트에 없는 미상 키 — 보수적으로 제거(표현일 수 있음).
        removed.append(f"unallowed:{key}")

    abstracted["raw_text_removed"] = raw_text_removed
    abstracted["image_pixels_removed"] = image_pixels_removed
    abstracted["removed_items"] = removed
    if leak_probe:
        abstracted["_leak_probe"] = leak_probe
    return abstracted, removed


def is_fully_abstracted(abstracted_feature: dict) -> bool:
    """추상화가 완전한지(원본 표현 잔존 없음) 판정.

    guard() 가 반환한 abstracted_feature 본문에 §9.2 제거 대상 키가 단 하나도
    남아 있지 않으면 True. (guard 는 제거 대상·미상 키를 모두 솎아내므로,
    정상 동작 시 항상 True. 외부에서 직접 조립한 feature 검증용 안전장치.)

    reconstruction test 및 릴리즈게이트 G4 의 사전조건.
    """
    for key in abstracted_feature:
        if key in REMOVAL_TARGETS:
            return False
    return True


def filter_features(features: Iterable[dict]) -> list[tuple[dict, list[str]]]:
    """배치 가드 편의 함수."""
    return [guard(f) for f in features]
