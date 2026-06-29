"""Brand risk DB 어댑터 (UPE Web Source Collector).

reuse_risk 엔진(동기)이 쓰는 brand_risk 값을 BrandRisk 테이블에서 프리페치해
dict 캐시 기반 동기 콜러블로 제공한다. 크로스레포 import 없음.
"""
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import BrandRisk
from app.pattern.reuse_risk import BrandRiskLookup


def _clamp01(value: float) -> float:
    """0~1 사이로 클램프."""
    return max(0.0, min(1.0, value))


async def load_brand_risk_map(
    session: AsyncSession,
    domains: Iterable[str] | None = None,
) -> dict[str, float]:
    """BrandRisk 테이블에서 도메인 → brand_risk 매핑을 로드.

    domains=None이면 전체, 아니면 부분집합. 빈 결과는 {}. Numeric→float, [0,1] clamp.
    """
    if domains is not None:
        domain_list = list(domains)
        if not domain_list:
            return {}
        stmt = select(BrandRisk).where(BrandRisk.domain.in_(domain_list))
    else:
        stmt = select(BrandRisk)

    result = await session.execute(stmt)
    rows = result.scalars().all()
    return {row.domain: _clamp01(float(row.brand_risk)) for row in rows}


def make_brand_risk_lookup(cache: dict[str, float]) -> BrandRiskLookup:
    """캐시 dict 기반 동기 콜러블 생성.

    load_brand_risk_map() 반환값을 받아 domain → brand_risk(float|None) 반환.
    없는 domain은 None(엔진이 기본 0.5로 폴백).
    """
    def lookup(domain: str) -> float | None:
        return cache.get(domain)

    return lookup


async def upsert_brand_risk(
    session: AsyncSession,
    domain: str,
    brand_risk: float,
    note: str | None = None,
) -> None:
    """BrandRisk upsert. select 후 있으면 update, 없으면 insert(이식성 우선).

    brand_risk [0,1] clamp. flush까지 수행(커밋은 호출자).
    """
    clamped = _clamp01(brand_risk)
    stmt = select(BrandRisk).where(BrandRisk.domain == domain)
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing is not None:
        existing.brand_risk = clamped
        if note is not None:
            existing.note = note
    else:
        session.add(BrandRisk(domain=domain, brand_risk=clamped, note=note))

    await session.flush()