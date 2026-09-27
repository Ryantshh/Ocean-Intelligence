"""Install the additive PostgreSQL recommendation ledger schema.

Run: python -m ai_platform.recommendations.migrate
Uses the application's configured data database. Does not alter source tables.
"""

import asyncio
from pathlib import Path

from ai_platform.backend.db import _get_pool, close_pool


async def main():
    try:
        pool = await _get_pool()
        async with pool.acquire() as connection:
            root = Path(__file__).resolve().parents[2] / "infra" / "sql"
            for filename in ("recommendation_ledger.sql", "desk_operations.sql"):
                await connection.execute((root / filename).read_text())
        print("Recommendation ledger and desk operations schema installed.")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
