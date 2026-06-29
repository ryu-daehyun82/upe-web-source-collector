from app.adapters.vision import (
    DetectionResult, FallbackLogoDetector, FallbackArtworkDetector,
    get_logo_detector, get_artwork_detector, detect_visual_flags,
    LOGO_SCORE_THRESHOLD, ARTWORK_SCORE_THRESHOLD, ENTROPY_HIGH, STOCK_MATCH_LOW,
)


class TestFallbackLogoDetector:
    """FallbackLogoDetector 단위 테스트"""

    def setup_method(self):
        self.detector = FallbackLogoDetector()

    def test_logo_empty(self):
        result = self.detector.detect({})
        assert isinstance(result, DetectionResult)
        assert result.detected is False
        assert result.confidence == 0.0
        assert result.reason is None

    def test_logo_explicit_true(self):
        result = self.detector.detect({"logo_detected": True})
        assert result.detected is True
        assert result.confidence == 1.0
        assert result.reason == "explicit_flag"

    def test_logo_explicit_false(self):
        result = self.detector.detect({"logo_detected": False})
        assert result.detected is False
        assert result.reason == "explicit_flag"

    def test_logo_score_high(self):
        result = self.detector.detect({"logo_score": 0.8})
        assert result.detected is True
        assert result.confidence == 0.8
        assert result.reason == "score"

    def test_logo_score_low(self):
        result = self.detector.detect({"logo_score": 0.3})
        assert result.detected is False
        assert result.reason == "score"

    def test_logo_heuristic_vector(self):
        result = self.detector.detect({"vector_graphic": True})
        assert result.detected is True
        assert result.reason == "heuristic"

    def test_logo_heuristic_alpha_regions(self):
        result = self.detector.detect({
            "has_alpha_channel": True,
            "high_contrast_region_count": 2
        })
        assert result.detected is True

    def test_logo_heuristic_alpha_no_regions(self):
        result = self.detector.detect({
            "has_alpha_channel": True,
            "high_contrast_region_count": 0
        })
        assert result.detected is False

    def test_logo_priority_explicit_over_score(self):
        """명시 플래그가 score보다 우선"""
        result = self.detector.detect({
            "logo_detected": False,
            "logo_score": 0.9
        })
        assert result.detected is False
        assert result.reason == "explicit_flag"

    def test_logo_priority_score_over_heuristic(self):
        """score가 휴리스틱보다 우선"""
        result = self.detector.detect({
            "logo_score": 0.3,
            "vector_graphic": True
        })
        assert result.detected is False
        assert result.reason == "score"


class TestFallbackArtworkDetector:
    """FallbackArtworkDetector 단위 테스트"""

    def setup_method(self):
        self.detector = FallbackArtworkDetector()

    def test_artwork_explicit_true(self):
        result = self.detector.detect({"unique_artwork_detected": True})
        assert result.detected is True
        assert result.reason == "explicit_flag"

    def test_artwork_score_high(self):
        result = self.detector.detect({"artwork_score": 0.6})
        assert result.detected is True
        assert result.confidence == 0.6
        assert result.reason == "score"

    def test_artwork_score_low(self):
        result = self.detector.detect({"artwork_score": 0.4})
        assert result.detected is False
        assert result.reason == "score"

    def test_artwork_heuristic_entropy(self):
        result = self.detector.detect({
            "image_entropy": 7.5,
            "stock_match_score": 0.1
        })
        assert result.detected is True
        assert result.reason == "heuristic_entropy"

    def test_artwork_entropy_no_stock(self):
        result = self.detector.detect({"image_entropy": 7.5})
        assert result.detected is True

    def test_artwork_low_entropy(self):
        result = self.detector.detect({"image_entropy": 3.0})
        assert result.detected is False

    def test_artwork_high_entropy_high_stock(self):
        result = self.detector.detect({
            "image_entropy": 7.5,
            "stock_match_score": 0.9
        })
        assert result.detected is False

    def test_artwork_priority_explicit_over_score(self):
        """명시 플래그가 score보다 우선"""
        result = self.detector.detect({
            "unique_artwork_detected": False,
            "artwork_score": 0.9
        })
        assert result.detected is False
        assert result.reason == "explicit_flag"

    def test_artwork_priority_score_over_heuristic(self):
        """score가 휴리스틱보다 우선"""
        result = self.detector.detect({
            "artwork_score": 0.4,
            "image_entropy": 7.5,
            "stock_match_score": 0.05
        })
        assert result.detected is False
        assert result.reason == "score"

    def test_artwork_entropy_boundary_high(self):
        """엔트로피가 정확히 ENTROPY_HIGH인 경우"""
        result = self.detector.detect({
            "image_entropy": ENTROPY_HIGH,
            "stock_match_score": STOCK_MATCH_LOW
        })
        assert result.detected is True

    def test_artwork_entropy_boundary_low(self):
        """엔트로피가 ENTROPY_HIGH 미만인 경우"""
        result = self.detector.detect({
            "image_entropy": ENTROPY_HIGH - 0.1,
            "stock_match_score": STOCK_MATCH_LOW
        })
        assert result.detected is False

    def test_artwork_stock_match_boundary(self):
        """stock_match_score가 정확히 STOCK_MATCH_LOW인 경우"""
        result = self.detector.detect({
            "image_entropy": ENTROPY_HIGH + 0.5,
            "stock_match_score": STOCK_MATCH_LOW
        })
        assert result.detected is True

    def test_artwork_stock_match_exceeds_threshold(self):
        """stock_match_score가 STOCK_MATCH_LOW를 초과하는 경우"""
        result = self.detector.detect({
            "image_entropy": ENTROPY_HIGH + 0.5,
            "stock_match_score": STOCK_MATCH_LOW + 0.01
        })
        assert result.detected is False


class TestDetectVisualFlags:
    """detect_visual_flags 통합 동작 테스트"""

    def test_detect_visual_flags(self):
        result = detect_visual_flags({
            "logo_detected": True,
            "image_entropy": 7.5,
            "stock_match_score": 0.05
        })
        assert result == {"logo_detected": True, "unique_artwork_detected": True}

    def test_flags_all_false(self):
        result = detect_visual_flags({})
        assert result == {"logo_detected": False, "unique_artwork_detected": False}

    def test_only_logo_true(self):
        result = detect_visual_flags({"logo_detected": True})
        assert result == {"logo_detected": True, "unique_artwork_detected": False}

    def test_only_artwork_true(self):
        result = detect_visual_flags({
            "image_entropy": 7.5,
            "stock_match_score": 0.05
        })
        assert result == {"logo_detected": False, "unique_artwork_detected": True}

    def test_both_false_with_data(self):
        result = detect_visual_flags({
            "logo_score": 0.3,
            "image_entropy": 3.0
        })
        assert result == {"logo_detected": False, "unique_artwork_detected": False}


class TestGetDetectors:
    """get_logo_detector / get_artwork_detector 테스트"""

    def test_get_logo_detector_fallback(self):
        detector = get_logo_detector()
        assert detector.name == "fallback"
        assert isinstance(detector, FallbackLogoDetector)

    def test_get_artwork_detector_fallback(self):
        detector = get_artwork_detector()
        assert detector.name == "fallback"
        assert isinstance(detector, FallbackArtworkDetector)


class TestThresholdConstants:
    """상수 임계값 검증"""

    def test_logo_score_threshold(self):
        assert LOGO_SCORE_THRESHOLD == 0.5

    def test_artwork_score_threshold(self):
        assert ARTWORK_SCORE_THRESHOLD == 0.5

    def test_entropy_high(self):
        assert ENTROPY_HIGH == 7.0

    def test_stock_match_low(self):
        assert STOCK_MATCH_LOW == 0.2