"""DB 층 — async SQLAlchemy 2.0 + asyncpg (Sprint 0).

env `UPE_DATABASE_URL` 로 연결 문자열 주입(기본 로컬 postgres).
테스트는 sqlite+aiosqlite 로 동일 URL 환경변수만 바꿔 주입 가능.

async engine 은 모듈 임포트 시점에 lazy 하게 만들지 않고 즉시 생성하되,
URL 이 비유효해도 임포트는 깨지지 않도록(연결은 첫 세션 사용 시 시작) create_async_engine
의 지연 연결 특성을 활용한다.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DEFAULT_DATABASE_URL = "postgresql+asyncpg://localhost/upe"


def _database_url() -> str:
    return os.getenv("UPE_DATABASE_URL", DEFAULT_DATABASE_URL)


# 모듈 전역 엔진/세션팩토리. create_async_engine 은 실제 연결을 첫 사용까지 지연하므로
# 임포트만으로 DB 가 떠 있을 필요는 없다.
engine: AsyncEngine = create_async_engine(_database_url(), future=True, pool_pre_ping=True)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 의존성 — 요청 단위 AsyncSession.

    핸들러에서 `session: AsyncSession = Depends(get_session)` 로 주입.
    예외 시 롤백, 정상 종료 시 커밋은 핸들러가 명시적으로 수행(여기선 close 만 보장).
    """
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
