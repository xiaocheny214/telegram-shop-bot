import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import event, select

from shopbot.framework.config.database import DatabaseConfig
from shopbot.framework.db import begin_immediate, create_session_factory, init_db
from shopbot.shop.models import Category, Product, ProductDetail, ShopSetting, User
from shopbot.shop.product.service import get_product
from shopbot.shop.setting.service import get_shop_setting
from shopbot.system.scheduler.launcher import run_scheduler


def _begins(engine) -> list[str]:
    found: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(_conn, _cursor, statement, _params, _context, _executemany):
        normalized = " ".join(statement.split()).upper()
        if normalized.startswith("BEGIN"):
            found.append(normalized)

    return found


def test_existing_shop_setting_keeps_loaded_user(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = await init_db(DatabaseConfig(path=tmp_path / "setting.db"))
        factory = create_session_factory(engine)
        async with factory() as session:
            async with begin_immediate(session):
                session.add(ShopSetting(id=1, order_timeout_seconds=900, max_pending_orders=3))
                session.add(User(telegram_id=42, username="ada", role=1, is_active=True, language="zh"))
        begins = _begins(engine)
        async with factory() as session:
            buyer = await session.scalar(select(User).where(User.telegram_id == 42))
            setting = await get_shop_setting(session)
            assert buyer.balance_usdt_cents == 0
            assert setting.order_timeout_seconds == 900
        assert begins == ["BEGIN"]
        await engine.dispose()

    asyncio.run(scenario())


def test_missing_shop_setting_inserts_without_expiring_user(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = await init_db(DatabaseConfig(path=tmp_path / "insert-setting.db"))
        factory = create_session_factory(engine)
        async with factory() as session:
            async with begin_immediate(session):
                session.add(User(telegram_id=42, username="ada", role=1, is_active=True, language="zh"))
        begins = _begins(engine)
        async with factory() as session:
            buyer = await session.scalar(select(User).where(User.telegram_id == 42))
            setting = await get_shop_setting(session)
            assert buyer.balance_usdt_cents == 0
            assert setting.id == 1
            assert setting.order_timeout_seconds == 900
        assert begins == ["BEGIN", "BEGIN IMMEDIATE"]
        await engine.dispose()

    asyncio.run(scenario())


def test_existing_product_detail_stays_on_the_read_transaction(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = await init_db(DatabaseConfig(path=tmp_path / "product.db"))
        factory = create_session_factory(engine)
        async with factory() as session:
            async with begin_immediate(session):
                session.add(User(telegram_id=7, username="ada", role=1, is_active=True, language="zh"))
                category = Category(name="cards")
                session.add(category)
                await session.flush()
                product = Product(category_id=category.id, name="month", price_cents=100)
                session.add(product)
                await session.flush()
                product_id = product.id
                session.add(ProductDetail(product_id=product_id, description="ready"))
        begins = _begins(engine)
        async with factory() as session:
            buyer = await session.scalar(select(User).where(User.telegram_id == 7))
            product, detail = await get_product(session, product_id)
            assert buyer.balance_usdt_cents == 0
            assert product.name == "month"
            assert detail.description == "ready"
        assert begins == ["BEGIN"]
        await engine.dispose()

    asyncio.run(scenario())


def test_missing_product_detail_inserts_without_expiring_user(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = await init_db(DatabaseConfig(path=tmp_path / "detail.db"))
        factory = create_session_factory(engine)
        async with factory() as session:
            async with begin_immediate(session):
                session.add(User(telegram_id=7, username="ada", role=1, is_active=True, language="zh"))
                category = Category(name="cards")
                session.add(category)
                await session.flush()
                product = Product(category_id=category.id, name="month", price_cents=100)
                session.add(product)
                await session.flush()
                product_id = product.id
        begins = _begins(engine)
        async with factory() as session:
            buyer = await session.scalar(select(User).where(User.telegram_id == 7))
            product, detail = await get_product(session, product_id)
            assert buyer.balance_usdt_cents == 0
            assert product.price_cents == 100
            assert detail.description == ""
        assert begins == ["BEGIN", "BEGIN IMMEDIATE"]
        await engine.dispose()

    asyncio.run(scenario())


def test_detail_insert_joins_the_caller_write_transaction(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = await init_db(DatabaseConfig(path=tmp_path / "join.db"))
        factory = create_session_factory(engine)
        async with factory() as session:
            try:
                async with begin_immediate(session):
                    category = Category(name="cards")
                    session.add(category)
                    await session.flush()
                    product = Product(category_id=category.id, name="month", price_cents=100)
                    session.add(product)
                    await session.flush()
                    product_id = product.id
                    _product, detail = await get_product(session, product_id)
                    assert detail.product_id == product_id
                    raise RuntimeError("stop")
            except RuntimeError as exc:
                if str(exc) != "stop":
                    raise
        async with factory() as session:
            assert await session.get(Product, product_id) is None
            assert await session.get(ProductDetail, product_id) is None
        await engine.dispose()

    asyncio.run(scenario())


def test_optimize_failure_still_runs_scheduled_work() -> None:
    async def scenario() -> None:
        optimize = AsyncMock(side_effect=RuntimeError("optimize down"))
        run_due = AsyncMock(return_value=1.0)
        sleeps = 0

        async def sleep(_delay: float) -> None:
            nonlocal sleeps
            sleeps += 1
            if sleeps >= 2:
                raise asyncio.CancelledError

        with (
            patch("shopbot.system.scheduler.launcher.optimize", optimize),
            patch("shopbot.system.scheduler.launcher.run_due", run_due),
            patch("shopbot.system.scheduler.launcher.asyncio.sleep", sleep),
        ):
            try:
                await run_scheduler(type("Factory", (), {"kw": {"bind": object()}})())
            except asyncio.CancelledError:
                pass
        assert optimize.await_count == 2
        assert run_due.await_count == 2

    asyncio.run(scenario())
