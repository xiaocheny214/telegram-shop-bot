import asyncio
from pathlib import Path

from sqlalchemy import event, text
from shopbot.framework.config.database import DatabaseConfig
from shopbot.framework.db import begin_immediate, create_engine, create_session_factory


def _capture(engine) -> list[str]:
    begins: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(_conn, _cursor, statement, _params, _context, _executemany):
        normalized = " ".join(statement.split()).upper()
        if normalized.startswith("BEGIN"):
            begins.append(normalized)

    return begins


def test_read_uses_deferred_begin(tmp_path: Path) -> None:
    engine = create_engine(DatabaseConfig(path=tmp_path / "read.db"))
    factory = create_session_factory(engine)
    begins = _capture(engine)

    async def read() -> None:
        async with factory() as session:
            await session.execute(text("SELECT 1"))
        await engine.dispose()

    asyncio.run(read())
    assert begins == ["BEGIN"]


def test_begin_immediate_takes_write_lock(tmp_path: Path) -> None:
    engine = create_engine(DatabaseConfig(path=tmp_path / "write.db", busy_timeout_ms=200))
    factory = create_session_factory(engine)
    begins = _capture(engine)

    async def write() -> None:
        async with factory() as session:
            async with begin_immediate(session):
                await session.execute(text("CREATE TABLE IF NOT EXISTS marks (id INTEGER PRIMARY KEY)"))
        await engine.dispose()

    asyncio.run(write())
    assert begins[0] == "BEGIN IMMEDIATE"


def test_deferred_reader_does_not_block_writer(tmp_path: Path) -> None:
    path = tmp_path / "parallel.db"
    engine = create_engine(DatabaseConfig(path=path, busy_timeout_ms=200))
    factory = create_session_factory(engine)

    async def scenario() -> None:
        async with factory() as setup:
            async with begin_immediate(setup):
                await setup.execute(text("CREATE TABLE marks (id INTEGER PRIMARY KEY)"))
        reader = factory()
        session = await reader.__aenter__()
        await session.execute(text("SELECT * FROM marks"))
        try:
            async with factory() as writer:
                async with begin_immediate(writer):
                    await writer.execute(text("INSERT INTO marks (id) VALUES (1)"))
        finally:
            await reader.__aexit__(None, None, None)
        await engine.dispose()

    asyncio.run(asyncio.wait_for(scenario(), timeout=2))
