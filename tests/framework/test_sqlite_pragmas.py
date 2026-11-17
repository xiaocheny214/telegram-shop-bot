import asyncio
from pathlib import Path

from sqlalchemy import text

from shopbot.framework.config.database import DatabaseConfig
from shopbot.framework.db import create_engine


def test_connection_pragmas(tmp_path: Path) -> None:
    config = DatabaseConfig(path=tmp_path / "shop.db")

    async def read() -> dict[str, object]:
        engine = create_engine(config)
        try:
            async with engine.connect() as connection:
                rows = {}
                for name in (
                    "journal_mode",
                    "synchronous",
                    "foreign_keys",
                    "busy_timeout",
                    "cache_size",
                    "temp_store",
                    "journal_size_limit",
                ):
                    rows[name] = (await connection.execute(text(f"PRAGMA {name}"))).scalar_one()
                return rows
        finally:
            await engine.dispose()

    got = asyncio.run(read())
    assert got["journal_mode"] == "wal"
    assert got["synchronous"] == 1  # NORMAL
    assert got["foreign_keys"] == 1
    assert got["busy_timeout"] == 5000
    assert got["cache_size"] == -8000
    assert got["temp_store"] == 2  # MEMORY
    assert got["journal_size_limit"] == 67_108_864
