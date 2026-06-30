"""릴리즈 게이트 (§16) — CI 에서 pytest 외에 추가 검증.

G0: DDL create_all dry-run(sqlite).
G4: Golden Set critical 라벨 recall=100% 강제.
실패 시 비정상 종료(exit 1)로 CI 실패 처리.
"""
from __future__ import annotations

import asyncio
import os
import sys

# cwd/실행 위치 무관하게 repo 루트(이 파일의 상위)를 import 경로에 추가.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.models.tables import Base  # noqa: E402
from app.golden_set import evaluate_golden_set  # noqa: E402


async def _g0_ddl_dry_run() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print("G0 DDL create_all: OK")


def _g4_golden_gate() -> bool:
    report = evaluate_golden_set()
    print("G4 recall_by_label:", report.recall_by_label)
    if not report.passed:
        print("G4 Golden Set gate: FAILED — critical recall < 100%")
        return False
    print("G4 Golden Set gate: PASSED")
    return True


def main() -> int:
    asyncio.run(_g0_ddl_dry_run())
    if not _g4_golden_gate():
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())