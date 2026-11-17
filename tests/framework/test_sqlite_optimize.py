import asyncio
from pathlib import Path

from shopbot.framework.config.database import DatabaseConfig
from shopbot.framework.db import init_db, optimize, optimize_due


def test_optimize_due_once_per_hour() -> None:
    assert optimize_due(0, 3599) is False
    assert optimize_due(0, 3600) is True


def test_optimize_runs_on_open_database(tmp_path: Path) -> None:
    path = tmp_path / "stats.db"

    async def run() -> None:
        engine = await init_db(DatabaseConfig(path=path))
        await optimize(engine)
        await engine.dispose()

    asyncio.run(run())
    assert path.exists()
