"""근사중복(near-dup) 탐지 모듈 — simhash 기반 (v2.1 P-6 / §13.3 / §14).

content_hash(정확일치) 외에 simhash 64-bit 지문으로 근사중복을 잡아
web_sources.near_dup_key 를 채우고 dedup 단위·골든 테스트에 쓴다.

stored key 재현성: near_dup_key 는 DB 에 저장 후 재계산·비교되므로 프로세스마다
salt 가 다른 내장 hash() 를 쓰지 않고, stdlib blake2b(digest_size=8) 안정 해시를 쓴다.
순수 모듈(stdlib 만).
"""
import hashlib
import re

SIMHASH_BITS = 64
SHINGLE_K = 4

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """텍스트를 소문자 단어 토큰 리스트로 변환."""
    return _TOKEN_RE.findall(text.lower())


def _shingles(tokens: list[str], k: int) -> set[str]:
    """토큰 리스트에서 k-토큰 shingle 집합 생성. len<k 면 전체를 단일 shingle 로."""
    if not tokens:
        return set()
    if len(tokens) < k:
        return {" ".join(tokens)}
    return {" ".join(tokens[i:i + k]) for i in range(len(tokens) - k + 1)}


def _stable_hash64(s: str) -> int:
    """문자열의 안정적인 64-bit 해시(blake2b, 프로세스 무관 재현)."""
    h = hashlib.blake2b(s.encode("utf-8"), digest_size=8)
    return int.from_bytes(h.digest(), "big")


def simhash(text: str | None, *, k: int = SHINGLE_K, bits: int = SIMHASH_BITS) -> int:
    """텍스트의 simhash(정수). 빈/None/토큰없음 → 0.

    토큰화→k-shingle→각 shingle 안정해시→비트별 +1/-1 누적→누적>0 인 비트만 1.
    """
    if not text:
        return 0
    tokens = _tokenize(text)
    if not tokens:
        return 0
    shingle_set = _shingles(tokens, k)
    if not shingle_set:
        return 0

    v = [0] * bits
    for shingle in shingle_set:
        h = _stable_hash64(shingle)
        for i in range(bits):
            if h & (1 << i):
                v[i] += 1
            else:
                v[i] -= 1

    sig = 0
    for i in range(bits):
        if v[i] > 0:
            sig |= (1 << i)
    return sig


def near_dup_key(text: str | None, *, k: int = SHINGLE_K, bits: int = SIMHASH_BITS) -> str | None:
    """근사중복 키(고정폭 hex). 빈 콘텐츠(simhash==0)는 None(dedup 대상 아님)."""
    sig = simhash(text, k=k, bits=bits)
    if sig == 0:
        return None
    return f"{sig:0{bits // 4}x}"


def hamming(a: int, b: int) -> int:
    """두 정수 simhash 의 해밍거리(XOR 1비트 수)."""
    return bin(a ^ b).count("1")


def is_near_dup(
    a: str,
    b: str,
    *,
    k: int = SHINGLE_K,
    bits: int = SIMHASH_BITS,
    max_distance: int = 3,
) -> bool:
    """두 텍스트 근사중복 여부(해밍 <= max_distance). 빈 콘텐츠 한쪽이라도면 False."""
    sig_a = simhash(a, k=k, bits=bits)
    sig_b = simhash(b, k=k, bits=bits)
    if sig_a == 0 or sig_b == 0:
        return False
    return hamming(sig_a, sig_b) <= max_distance


def similarity(a: str, b: str, *, k: int = SHINGLE_K, bits: int = SIMHASH_BITS) -> float:
    """두 텍스트 simhash 유사도 = 1.0 - 해밍/비트수 ∈ [0,1]."""
    sig_a = simhash(a, k=k, bits=bits)
    sig_b = simhash(b, k=k, bits=bits)
    return 1.0 - hamming(sig_a, sig_b) / bits


def key_distance(key_a: str, key_b: str) -> int:
    """near_dup_key(hex) 두 개의 해밍거리."""
    return hamming(int(key_a, 16), int(key_b, 16))