"""PII 스캐너 어댑터 및 폴백 (§13.4 / §10).

외부 harvester pii_filter 를 import 가능하면 위임하고, 불가하면 self-contained
한국 PII 정규식 폴백 스캐너를 쓴다. 어댑터로 인터페이스를 고정해 실 어댑터를
투명 교체한다(현재 외부 레포 부재 → 폴백 활성).
"""
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, field

from app.models.enums import PiiStatus


@dataclass(frozen=True)
class PiiFinding:
    """PII 발견 항목.

    pii_type: "email" | "phone_kr" | "rrn" | "brn" | "credit_card"
    masked: 마스킹된 값(원본 PII 누출 방지).
    """
    pii_type: str
    masked: str


@dataclass
class PiiScanResult:
    """PII 스캔 결과(findings + 종합 status)."""
    findings: list[PiiFinding] = field(default_factory=list)
    status: PiiStatus = PiiStatus.clean

    def has_pii(self) -> bool:
        """findings 가 비어있지 않으면 True."""
        return len(self.findings) > 0

    def types(self) -> set[str]:
        """발견된 pii_type 집합."""
        return {finding.pii_type for finding in self.findings}


def _mask(value: str) -> str:
    """앞 2글자만 노출, 나머지 '*'. 길이 2 이하면 전부 '*'."""
    if len(value) <= 2:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 2)


# 고위험(=sensitive): 즉시 격리/검토 필요
_HIGH_RISK = frozenset({"rrn", "brn", "credit_card"})

# 정규식 (순서 중요: 더 구체적인 것 먼저)
_PATTERNS = [
    ("rrn",         re.compile(r"\b\d{6}-\d{7}\b")),                 # 주민등록번호 000000-0000000
    ("brn",         re.compile(r"\b\d{3}-\d{2}-\d{5}\b")),          # 사업자등록번호 000-00-00000
    ("credit_card", re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{4}\b")),    # 카드번호
    ("phone_kr",    re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b")), # 휴대폰
    ("email",       re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
]


class FallbackPiiScanner:
    """self-contained 한국 PII 정규식 폴백 스캐너."""
    name = "fallback"

    def scan(self, text: str | None) -> PiiScanResult:
        """텍스트에서 PII 탐지. 빈 텍스트면 clean."""
        if not text:
            return PiiScanResult()

        result = PiiScanResult()
        occupied_spans: set[tuple[int, int]] = set()

        for pii_type, pattern in _PATTERNS:
            for match in pattern.finditer(text):
                start, end = match.span()
                # 이미 (앞선 구체 패턴이) 점유한 구간과 겹치면 중복 집계 방지.
                if any(max(s, start) < min(e, end) for s, e in occupied_spans):
                    continue
                occupied_spans.add((start, end))
                result.findings.append(PiiFinding(pii_type, _mask(match.group())))

        if result.findings:
            if any(f.pii_type in _HIGH_RISK for f in result.findings):
                result.status = PiiStatus.sensitive
            else:
                result.status = PiiStatus.redacted

        return result


class HarvesterPiiScanner:
    """외부 harvester pii_filter 위임 래퍼. import 성공 시에만 인스턴스화."""
    name = "harvester"

    def __init__(self, impl) -> None:
        self._impl = impl

    def scan(self, text: str | None) -> PiiScanResult:
        """외부 구현 위임. 본 파일럿은 결과 매핑이 불명확하므로 폴백으로 위임
        (추후 실제 harvester 결과 → PiiScanResult 매핑으로 교체할 자리)."""
        return FallbackPiiScanner().scan(text)


def _try_load_harvester():
    """외부 harvester 모듈 로드 시도. 실패(ImportError) 시 None."""
    try:
        return importlib.import_module("emotional_support_harvester.pii_filter")
    except ImportError:
        return None


def get_pii_scanner():
    """가용 PII 스캐너 반환. harvester 있으면 위임 래퍼, 없으면 폴백."""
    mod = _try_load_harvester()
    if mod is not None:
        return HarvesterPiiScanner(mod)
    return FallbackPiiScanner()