from app.dedup import (
    SIMHASH_BITS, SHINGLE_K,
    simhash, near_dup_key, hamming, is_near_dup, similarity, key_distance,
)


def test_identical_text_same_hash():
    text = "the quick brown fox jumps over the lazy dog near the river bank every single morning"
    h1 = simhash(text)
    h2 = simhash(text)
    assert h1 == h2
    assert hamming(h1, h2) == 0
    assert similarity(text, text) == 1.0
    assert is_near_dup(text, text) is True


def test_empty_and_none():
    assert simhash("") == 0
    assert simhash(None) == 0
    assert near_dup_key("") is None
    assert near_dup_key(None) is None


def test_near_dup_key_format():
    text = "the quick brown fox jumps over the lazy dog near the river bank every single morning"
    key = near_dup_key(text)
    assert key is not None
    assert len(key) == 16
    int(key, 16)  # should not raise ValueError


def test_completely_different_not_near_dup():
    text_a = "the quick brown fox jumps over the lazy dog near the river bank every single morning"
    text_b = "quantum physics explains the behavior of matter and energy at the smallest scales elementary particles"
    h_a = simhash(text_a)
    h_b = simhash(text_b)
    assert hamming(h_a, h_b) > 3
    assert is_near_dup(text_a, text_b, max_distance=3) is False


def test_one_word_difference_is_near():
    # 한 단어만 다른 문장쌍은 "변동은 있으나(>0)", 전혀 다른 문서보다 훨씬 가깝다.
    text_a = "the quick brown fox jumps over the lazy dog near the river bank every single morning"
    text_b = "the quick brown fox jumps over the lazy cat near the river bank every single morning"
    text_diff = "quantum physics explains the behavior of matter and energy at the smallest scales elementary particles"
    d_near = hamming(simhash(text_a), simhash(text_b))
    d_far = hamming(simhash(text_a), simhash(text_diff))
    assert 0 < d_near < d_far  # 한 단어 차이 < 완전히 다른 문서
    assert is_near_dup(text_a, text_b, max_distance=d_near) is True


def test_empty_side_not_dup():
    text = "the quick brown fox jumps over the lazy dog near the river bank every single morning"
    assert is_near_dup("", text) is False
    assert is_near_dup(text, "") is False


def test_hamming_basic():
    assert hamming(0b1011, 0b1001) == 1
    assert hamming(0, 0) == 0
    assert hamming(0xFF, 0x00) == 8


def test_key_distance_matches_hamming():
    text_a = "the quick brown fox jumps over the lazy dog near the river bank every single morning"
    text_b = "the quick brown fox jumps over the lazy cat near the river bank every single morning"
    key_a = near_dup_key(text_a)
    key_b = near_dup_key(text_b)
    assert key_a is not None and key_b is not None
    assert key_distance(key_a, key_b) == hamming(simhash(text_a), simhash(text_b))


def test_similarity_range():
    text_a = "the quick brown fox jumps over the lazy dog near the river bank every single morning"
    text_b = "quantum physics explains the behavior of matter and energy at the smallest scales elementary particles"
    sim = similarity(text_a, text_b)
    assert 0.0 <= sim <= 1.0