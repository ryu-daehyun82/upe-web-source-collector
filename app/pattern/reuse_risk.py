"""Original Reuse Risk Score 엔진 (설계서 §9.4 + 스파이크 §1~§3).

UPE 의 법적 방어선이자 핵심 IP. `original_reuse_risk ∈ [0,1]` =
"이 패턴이 원본 표현을 얼마나 재현·역복원 가능한가" 의 점수(높을수록 위험).

설계 원칙 3가지(스파이크 §0):
  1. 하드룰 우선 — 로고/얼굴/고유아트/원문누출은 점수와 무관하게 즉시 high/blocked.
  2. 보수적 결합 — 약한 sub-score 여럿보다 강한 1개가 지배(max-bias).
  3. 역복원 불가 증명 — 점수가 낮아도 reconstruction test(별도 모듈)로 검증.

순수 모듈: 입력은 feature dict. 실 검출기(logo/face/artwork)는 boolean 플래그로
받는다. brand_risk 는 입력값 우선, 없으면 brand_risk 테이블 조회 어댑터(주입)로.
크로스레포 import 없음 — datasketch 미사용, 간이 MinHash 자체 구현.

가중치/임계는 모듈 상수로 분리(golden set 보정 대비, 스파이크 §4).
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Sequence

from app.models.enums import ReuseRisk

# ----------------------------------------------------------------------------
# 보정 가능한 상수 (스파이크 §2/§3/§4 — golden set 으로 보정할 초기값. 고정 아님)
# ----------------------------------------------------------------------------

#: 가중 결합식의 sub-score 가중치 (스파이크 §2). 합 = 0.90, 나머지 0.10 은 max-bias.
WEIGHTS: dict[str, float] = {
    "layout_similarity": 0.30,
    "color_signature": 0.20,
    "structure_fingerprint": 0.25,
    "brand_risk": 0.15,
    "max_bias": 0.10,  # 0.10 * max(layout_similarity, color_signature) — 강신호 지배
}

#: 원문 누출 하드룰 임계 (스파이크 §2). text_overlap 이 이 값 이상이면 즉시 blocked.
HARDRULE_TEXT_OVERLAP: float = 0.15

#: 원문 누출 시 강제 점수(스파이크 §2 `('blocked', 0.95)`).
HARDRULE_TEXT_LEAK_SCORE: float = 0.95

#: 고유 아트워크 검출 시 최소 점수(스파이크 §2 `max(0.75, score)`).
HARDRULE_ARTWORK_FLOOR: float = 0.75

#: 로고/얼굴 검출 시 risk_floor(스파이크 §2). 이 값 이하로 못 내려감.
HARDRULE_LOGO_FACE_FLOOR: float = 0.65

#: 등급 경계(스파이크 §3). (상한, 등급) 오름차순. score <= 상한 인 첫 등급.
#:   0.00~0.30 low / 0.31~0.60 medium / 0.61~0.80 high / 0.81~1.00 blocked
GRADE_THRESHOLDS: Sequence[tuple[float, ReuseRisk]] = (
    (0.30, ReuseRisk.low),
    (0.60, ReuseRisk.medium),
    (0.80, ReuseRisk.high),
    (1.00, ReuseRisk.blocked),
)

#: MinHash shingle 크기 (스파이크 §1: k=5 토큰 shingle).
SHINGLE_K: int = 5

#: MinHash 순열(해시 함수) 개수 — 추정 분산 ↓.
MINHASH_PERMS: int = 128


# ----------------------------------------------------------------------------
# 어댑터 인터페이스 (실 검출기 연결 지점 — 추후 주입)
# ----------------------------------------------------------------------------
# brand_risk 테이블 조회 시그니처. domain -> brand_risk[0,1].
#   연결지점: app.models.tables.BrandRisk (DB). Sprint 0 는 입력값/주입 콜러블만.
BrandRiskLookup = Callable[[str], float | None]

# 실 검출기 어댑터(추후 연결, 현재는 boolean 입력으로 대체):
#   logo_detected      → 템플릿매칭+소형 분류기 (신규)
#   face_detected      → makeup-ai-py identity_guard(insightface)  # noqa: 연결지점
#   unique_artwork     → 독창 일러스트/사진 분류기 (신규)
#   layout_similarity  → gcr-eare vision_layout/pixel_segmenter    # noqa: 연결지점
#   color_signature    → gcr-eare delta(색 시그니처)               # noqa: 연결지점


# ----------------------------------------------------------------------------
# 8개 sub-score 알고리즘 (각 [0,1], 높을수록 원본 근접) — 스파이크 §1
# ----------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _shingles(tokens: Sequence[str], k: int) -> set[str]:
    """k-토큰 shingle 집합(스파이크 §1: k=5)."""
    if k <= 0:
        k = 1
    if len(tokens) < k:
        # 토큰이 k 미만이면 전체를 단일 shingle 로(짧은 텍스트도 비교 가능).
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


def _hash64(s: str, seed: int) -> int:
    """결정적 64-bit 해시(자체 구현, datasketch 미사용).

    FNV-1a 변형 + seed mixing. 외부 의존 없이 안정적이고 재현 가능.
    """
    h = (1469598103934665603 ^ (seed * 0x9E3779B97F4A7C15)) & 0xFFFFFFFFFFFFFFFF
    for ch in s:
        h ^= ord(ch)
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    # 최종 mix(splitmix64 finalizer) — 분포 개선.
    h ^= h >> 30
    h = (h * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    h ^= h >> 27
    h = (h * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    h ^= h >> 31
    return h


def _minhash_signature(shingles: set[str], num_perms: int) -> list[int]:
    """간이 MinHash 시그니처(자체 구현). 각 순열의 최소 해시값."""
    if not shingles:
        return [0] * num_perms
    sig: list[int] = []
    for seed in range(num_perms):
        sig.append(min(_hash64(sh, seed) for sh in shingles))
    return sig


def _minhash_jaccard(sig_a: list[int], sig_b: list[int]) -> float:
    """두 MinHash 시그니처의 일치율 ≈ Jaccard 유사도."""
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    match = sum(1 for a, b in zip(sig_a, sig_b) if a == b)
    return match / len(sig_a)


def text_overlap(
    original_text: str | None,
    pattern_text: str | None,
    *,
    k: int = SHINGLE_K,
    num_perms: int = MINHASH_PERMS,
) -> float:
    """원문 vs 패턴 보존 텍스트의 MinHash/Shingling Jaccard (스파이크 §1).

    패턴은 원칙적으로 원문 미보존이므로 정상 = 0.0; > 0 이면 원문 누출 경보.
    누출 탐지용 — 작은 양의 누출도 잡아야 함(brand-clone/text-leak recall 100%).
    """
    if not original_text or not pattern_text:
        return 0.0
    orig_tokens = _tokenize(original_text)
    pat_tokens = _tokenize(pattern_text)
    orig_sh = _shingles(orig_tokens, k)
    pat_sh = _shingles(pat_tokens, k)
    if not orig_sh or not pat_sh:
        return 0.0
    # 작은 입력은 정확 Jaccard(분산 0), 큰 입력은 MinHash 추정.
    if len(orig_sh) <= num_perms and len(pat_sh) <= num_perms:
        inter = len(orig_sh & pat_sh)
        union = len(orig_sh | pat_sh)
        return inter / union if union else 0.0
    sig_o = _minhash_signature(orig_sh, num_perms)
    sig_p = _minhash_signature(pat_sh, num_perms)
    return _minhash_jaccard(sig_o, sig_p)


def _edit_distance(a: Sequence, b: Sequence) -> int:
    """Levenshtein 편집거리(순수 DP). 영역 트리 노드 시퀀스 비교용."""
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb]


def layout_similarity(feature: dict) -> float:
    """영역 트리의 정규화 편집거리(역) 또는 영역 IoU 평균 (스파이크 §1).

    입력 우선순위:
      1) feature['layout_similarity']  — 외부(gcr-eare) 사전계산값 [0,1]
      2) feature['region_iou'] 리스트   — 영역 IoU 평균
      3) feature['original_layout_tree'] vs feature['pattern_layout_tree']
         — 영역 라벨 시퀀스의 정규화 편집거리(1 - dist/maxlen)

    연결지점: gcr-eare vision_layout · pixel_segmenter.
    """
    pre = feature.get("layout_similarity")
    if isinstance(pre, (int, float)):
        return _clamp01(float(pre))

    ious = feature.get("region_iou")
    if isinstance(ious, (list, tuple)) and ious:
        vals = [float(x) for x in ious if isinstance(x, (int, float))]
        if vals:
            return _clamp01(sum(vals) / len(vals))

    orig = feature.get("original_layout_tree")
    pat = feature.get("pattern_layout_tree")
    seq_o = _layout_seq(orig)
    seq_p = _layout_seq(pat)
    if not seq_o and not seq_p:
        return 0.0
    dist = _edit_distance(seq_o, seq_p)
    max_len = max(len(seq_o), len(seq_p)) or 1
    return _clamp01(1.0 - dist / max_len)


def _layout_seq(tree) -> list[str]:
    """레이아웃 트리를 노드 라벨 시퀀스(전위순회)로 평탄화."""
    out: list[str] = []

    def _walk(node) -> None:
        if isinstance(node, dict):
            label = str(node.get("type") or node.get("tag") or node.get("label") or "node")
            out.append(label)
            children = node.get("children") or node.get("nodes") or []
            if isinstance(children, (list, tuple)):
                for c in children:
                    _walk(c)
        elif isinstance(node, (list, tuple)):
            for c in node:
                _walk(c)
        elif node is not None:
            out.append(str(node))

    _walk(tree)
    return out


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """코사인 유사도 [0,1] (음수는 0 으로 클램프)."""
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    av, bv = a[:n], b[:n]
    dot = sum(x * y for x, y in zip(av, bv))
    na = math.sqrt(sum(x * x for x in av))
    nb = math.sqrt(sum(y * y for y in bv))
    if na == 0 or nb == 0:
        return 0.0
    return _clamp01(dot / (na * nb))


def color_signature(feature: dict) -> float:
    """색 히스토그램 거리의 역(유사도) — 브랜드 팔레트 근접도 (스파이크 §1).

    입력 우선순위:
      1) feature['color_signature']  — 외부(gcr-eare delta) 사전계산값 [0,1]
      2) feature['original_color_hist'] vs feature['pattern_color_hist']
         — 정규화 히스토그램(예: HSV 8x8x8 평탄화) 코사인 유사도

    연결지점: gcr-eare delta(색 시그니처).
    """
    pre = feature.get("color_signature")
    if isinstance(pre, (int, float)):
        return _clamp01(float(pre))

    h_o = feature.get("original_color_hist")
    h_p = feature.get("pattern_color_hist")
    if isinstance(h_o, (list, tuple)) and isinstance(h_p, (list, tuple)) and h_o and h_p:
        return _cosine(
            [float(x) for x in h_o],
            [float(x) for x in h_p],
        )
    return 0.0


def structure_fingerprint(feature: dict) -> float:
    """(섹션순서 + 영역비율 + 표/카드수) 벡터의 원본 근접도 (스파이크 §1, §9.3).

    "추상화 허용 대상"이 과하게 원본을 특정하는지 검사. 너무 높으면 구조 자체가
    원본 지문이 됨.

    입력 우선순위:
      1) feature['structure_fingerprint']  — 사전계산값 [0,1]
      2) feature['original_structure_vec'] vs feature['pattern_structure_vec']
         — 코사인 유사도(섹션수/영역비율/표수/카드수/슬라이드흐름 벡터)
    """
    pre = feature.get("structure_fingerprint")
    if isinstance(pre, (int, float)):
        return _clamp01(float(pre))

    v_o = feature.get("original_structure_vec")
    v_p = feature.get("pattern_structure_vec")
    if isinstance(v_o, (list, tuple)) and isinstance(v_p, (list, tuple)) and v_o and v_p:
        return _cosine(
            [float(x) for x in v_o],
            [float(x) for x in v_p],
        )
    return 0.0


def brand_risk(feature: dict, *, lookup: BrandRiskLookup | None = None) -> float:
    """브랜드 위험도 (스파이크 §1). 자사/공공 = 낮음, 강브랜드 = 높음.

    입력 우선순위:
      1) feature['brand_risk']  — 명시 입력값 [0,1]
      2) lookup(domain)         — brand_risk 테이블 조회 어댑터(주입)
      3) 기본값 0.5             — 미상(보수 중립)

    연결지점: app.models.tables.BrandRisk 테이블(domain PK, brand_risk numeric).
    """
    pre = feature.get("brand_risk")
    if isinstance(pre, (int, float)):
        return _clamp01(float(pre))

    domain = feature.get("domain")
    if lookup is not None and isinstance(domain, str) and domain:
        val = lookup(domain)
        if isinstance(val, (int, float)):
            return _clamp01(float(val))

    return 0.5


def _flag(feature: dict, key: str) -> bool:
    """boolean 플래그 추출(실 검출기 대체 입력)."""
    return bool(feature.get(key, False))


# ----------------------------------------------------------------------------
# 등급 매핑 (스파이크 §3)
# ----------------------------------------------------------------------------


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def score_to_risk(score: float) -> ReuseRisk:
    """연속 점수 → 등급(스파이크 §3). 경계 0.30/0.60/0.80."""
    s = _clamp01(score)
    for upper, grade in GRADE_THRESHOLDS:
        if s <= upper:
            return grade
    return ReuseRisk.blocked


# ----------------------------------------------------------------------------
# 결합식 (스파이크 §2): 하드룰 → 가중합 → 보수 보정
# ----------------------------------------------------------------------------


def compute_reuse_risk(
    feature: dict,
    *,
    brand_risk_lookup: BrandRiskLookup | None = None,
) -> dict:
    """Original Reuse Risk Score 산정(엔진 진입점).

    입력 feature dict 예시 키:
      - original_text / pattern_text                (text_overlap)
      - layout_similarity | region_iou | *_layout_tree
      - color_signature | *_color_hist
      - structure_fingerprint | *_structure_vec
      - brand_risk | domain                          (+ brand_risk_lookup)
      - logo_detected / face_detected / unique_artwork_detected (boolean)

    반환 dict:
      {
        "reuse_score": float[0,1],
        "reuse_risk": ReuseRisk,
        "reuse_hardrule": str | None,   # 발동 하드룰 이름(null=없음)
        "subscores": { ... 8개 sub-score ... },
      }
    (WebPattern.reuse_subscores / reuse_score / reuse_hardrule / original_reuse_risk 계약과 정합)
    """
    # --- sub-score 계산 (각 [0,1]) ---
    so_text = text_overlap(feature.get("original_text"), feature.get("pattern_text"))
    so_layout = layout_similarity(feature)
    so_color = color_signature(feature)
    so_struct = structure_fingerprint(feature)
    so_brand = brand_risk(feature, lookup=brand_risk_lookup)
    logo = _flag(feature, "logo_detected")
    face = _flag(feature, "face_detected")
    artwork = _flag(feature, "unique_artwork_detected")

    subscores: dict[str, float | bool] = {
        "text_overlap": round(so_text, 6),
        "layout_similarity": round(so_layout, 6),
        "color_signature": round(so_color, 6),
        "structure_fingerprint": round(so_struct, 6),
        "brand_risk": round(so_brand, 6),
        "logo": logo,
        "face": face,
        "unique_artwork": artwork,
    }

    # --- 2) 가중 결합 (연속 신호, max-bias) — 먼저 raw 계산 ---
    raw = (
        WEIGHTS["layout_similarity"] * so_layout
        + WEIGHTS["color_signature"] * so_color
        + WEIGHTS["structure_fingerprint"] * so_struct
        + WEIGHTS["brand_risk"] * so_brand
        + WEIGHTS["max_bias"] * max(so_layout, so_color)  # 강신호 지배
    )
    raw = _clamp01(raw)

    # --- 1) 하드룰 (즉시 결정 / risk_floor) — 우선순위 순서 (스파이크 §2) ---
    # (a) 원문 누출 → 즉시 blocked (최우선, 법적 사고 방지)
    if so_text >= HARDRULE_TEXT_OVERLAP:
        return _result(HARDRULE_TEXT_LEAK_SCORE, "text_overlap", subscores)

    # (b) 고유 아트워크 → high+ (max(0.75, raw))
    if artwork:
        score = max(HARDRULE_ARTWORK_FLOOR, raw)
        return _result(score, "unique_artwork", subscores)

    # (c) 로고/얼굴 → risk_floor 0.65 (이하로 못 내려감)
    risk_floor = HARDRULE_LOGO_FACE_FLOOR if (logo or face) else 0.0
    hardrule = "logo_or_face" if (logo or face) else None

    # --- 3) 보수 보정 ---
    score = max(raw, risk_floor)
    return _result(score, hardrule, subscores)


def _result(score: float, hardrule: str | None, subscores: dict) -> dict:
    score = _clamp01(score)
    return {
        "reuse_score": round(score, 6),
        "reuse_risk": score_to_risk(score),
        "reuse_hardrule": hardrule,
        "subscores": subscores,
    }


def compute_from_features(
    features: Iterable[dict],
    *,
    brand_risk_lookup: BrandRiskLookup | None = None,
) -> list[dict]:
    """배치 산정 편의 함수(golden set 평가용)."""
    return [compute_reuse_risk(f, brand_risk_lookup=brand_risk_lookup) for f in features]
