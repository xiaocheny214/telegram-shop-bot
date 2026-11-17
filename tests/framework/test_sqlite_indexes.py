import asyncio
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from shopbot.framework.config.database import DatabaseConfig
from shopbot.framework.db import begin_immediate, create_session_factory, init_db


def _index_names(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        connection.close()
    return {name for (name,) in rows}


def test_fresh_database_has_query_indexes(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"

    async def setup() -> None:
        engine = await init_db(DatabaseConfig(path=path))
        await engine.dispose()

    asyncio.run(setup())
    names = _index_names(path)
    assert {
        "ix_orders_user_created",
        "ix_wallet_recharges_user_created",
        "ix_wallet_ledgers_user_id_id",
        "ux_wallet_ledgers_ref",
        "ix_cards_claim",
        "ix_users_username",
    } <= names
    assert not names & {
        "ix_wallet_recharges_user_id",
        "ix_wallet_ledgers_user_id",
        "ix_cards_product_id",
        "ix_cards_status",
        "ix_cards_product_spec_status",
    }


def test_existing_database_gains_indexes(tmp_path: Path) -> None:
    path = tmp_path / "old.db"

    async def first() -> None:
        engine = await init_db(DatabaseConfig(path=path))
        await engine.dispose()

    asyncio.run(first())
    connection = sqlite3.connect(path)
    connection.execute("DROP INDEX IF EXISTS ix_orders_user_created")
    connection.execute("CREATE INDEX ix_cards_status ON cards (status)")
    connection.execute("CREATE INDEX ix_cards_product_id ON cards (product_id)")
    connection.execute("CREATE INDEX ix_cards_product_spec_status ON cards (product_id, spec_id, status)")
    connection.execute(
        "INSERT INTO users (telegram_id, username, role, is_active, language, balance_usdt_cents) "
        "VALUES (100, 'ada', 1, 1, 'zh', 0)"
    )
    connection.commit()
    connection.close()

    async def second() -> None:
        engine = await init_db(DatabaseConfig(path=path))
        await engine.dispose()

    asyncio.run(second())
    names = _index_names(path)
    assert "ix_orders_user_created" in names
    assert "ix_cards_claim" in names
    assert "ix_cards_status" not in names
    assert "ix_cards_product_id" not in names
    assert "ix_cards_product_spec_status" not in names
    connection = sqlite3.connect(path)
    try:
        username = connection.execute("SELECT username FROM users WHERE telegram_id = 100").fetchone()
    finally:
        connection.close()
    assert username == ("ada",)


def test_duplicate_ledger_ref_rejects_second_insert(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"

    async def insert_twice() -> None:
        engine = await init_db(DatabaseConfig(path=path))
        factory = create_session_factory(engine)
        async with factory() as session:
            async with begin_immediate(session):
                await session.execute(
                    text(
                        "INSERT INTO users (telegram_id, role, is_active, language, balance_usdt_cents) "
                        "VALUES (1, 1, 1, 'zh', 0)"
                    )
                )
                user_id = (await session.execute(text("SELECT id FROM users"))).scalar_one()
                await session.execute(
                    text(
                        "INSERT INTO wallet_ledgers (user_id, amount_usdt_cents, direction, reason, ref_type, ref_id) "
                        "VALUES (:user_id, 100, 'credit', '充值', 'recharge', 'R1')"
                    ),
                    {"user_id": user_id},
                )
        async with factory() as session:
            with pytest.raises(IntegrityError):
                async with begin_immediate(session):
                    await session.execute(
                        text(
                            "INSERT INTO wallet_ledgers (user_id, amount_usdt_cents, direction, reason, ref_type, ref_id) "
                            "VALUES (:user_id, 100, 'credit', '充值', 'recharge', 'R1')"
                        ),
                        {"user_id": user_id},
                    )
        await engine.dispose()

    asyncio.run(insert_twice())


def test_manual_ledgers_allow_multiple_null_refs(tmp_path: Path) -> None:
    path = tmp_path / "manual.db"

    async def insert_two() -> None:
        engine = await init_db(DatabaseConfig(path=path))
        factory = create_session_factory(engine)
        async with factory() as session:
            async with begin_immediate(session):
                await session.execute(
                    text(
                        "INSERT INTO users (telegram_id, role, is_active, language, balance_usdt_cents) "
                        "VALUES (1, 1, 1, 'zh', 0)"
                    )
                )
                user_id = (await session.execute(text("SELECT id FROM users"))).scalar_one()
                for _ in range(2):
                    await session.execute(
                        text(
                            "INSERT INTO wallet_ledgers (user_id, amount_usdt_cents, direction, reason) "
                            "VALUES (:user_id, 100, 'credit', '调整')"
                        ),
                        {"user_id": user_id},
                    )
        await engine.dispose()

    asyncio.run(insert_two())
