"""按 DatabaseConfig 创建引擎和会话。表声明在 shop.models。"""

import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, SessionTransactionOrigin

from shopbot.framework.config.database import DatabaseConfig


class Base(DeclarativeBase):
    pass


_active_begin: ContextVar[str] = ContextVar("shopbot_sqlite_begin", default="DEFERRED")


def create_engine(config: DatabaseConfig) -> AsyncEngine:
    engine = create_async_engine(config.url, echo=config.echo)
    timeout_ms = config.busy_timeout_ms

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        # 关掉驱动自己的 BEGIN。读事务发 BEGIN，写事务由 begin_immediate 发 BEGIN IMMEDIATE。
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        if config.wal:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={timeout_ms}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-8000")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.execute("PRAGMA journal_size_limit=67108864")
        cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def _begin(connection) -> None:
        if _active_begin.get() == "IMMEDIATE":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            return
        connection.exec_driver_sql("BEGIN")

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def _open_immediate(sync_session: Session) -> None:
    sync_session.info["sqlite_begin"] = "IMMEDIATE"
    token = _active_begin.set("IMMEDIATE")
    try:
        sync_session.connection()
    except Exception:
        sync_session.info["sqlite_begin"] = "DEFERRED"
        raise
    finally:
        _active_begin.reset(token)


@asynccontextmanager
async def begin_immediate(session: AsyncSession):
    """写事务。开始时拿 SQLite 写锁，成功后提交，失败后回滚。

    外层已经是立即写事务时直接复用。
    外层只是查询自动打开的事务、且没有待提交修改时，先回滚再重新以 IMMEDIATE 开始。
    回滚会使这次查询装载的对象过期。写入函数要使用自己查询或返回的对象。
    """
    sync = session.sync_session
    transaction = sync.get_transaction()
    if transaction is not None and (
        transaction.origin in {SessionTransactionOrigin.BEGIN, SessionTransactionOrigin.BEGIN_NESTED}
        or sync.info.get("sqlite_begin") == "IMMEDIATE"
    ):
        yield
        return
    if transaction is not None and transaction.origin is SessionTransactionOrigin.AUTOBEGIN:
        if sync.new or sync.dirty or sync.deleted:
            raise RuntimeError("写事务开始前会话里已有未提交修改")
        await session.rollback()
    await session.run_sync(_open_immediate)
    try:
        yield
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        sync.info["sqlite_begin"] = "DEFERRED"


@asynccontextmanager
async def ensure_transaction(session: AsyncSession):
    """没有显式事务时开启并在成功后提交，失败时回滚。"""
    async with begin_immediate(session):
        yield


def transactional[T](func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    @wraps(func)
    async def wrapped(session: AsyncSession, *args: Any, **kwargs: Any) -> T:
        async with ensure_transaction(session):
            return await func(session, *args, **kwargs)

    return wrapped


async def init_db(config: DatabaseConfig) -> AsyncEngine:
    import shopbot.shop.models  # noqa: F401
    import shopbot.system.models  # noqa: F401

    _allow_order_without_product(config.path)
    engine = create_engine(config)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_ensure_columns)
        await connection.run_sync(_ensure_indexes)
    await optimize(engine)
    return engine


# create_all 不会给已经存在的表加列。这两列是展示用的，旧库启动时补上。
_ADDED_COLUMNS = (
    ("shop_settings", "display_name", "VARCHAR(64)"),
    ("categories", "icon", "VARCHAR(16)"),
    ("wallet_ledgers", "operator_user_id", "INTEGER"),
)


def _ensure_columns(connection) -> None:
    for table, column, column_type in _ADDED_COLUMNS:
        rows = connection.execute(text(f"PRAGMA table_info({table})")).fetchall()
        if not rows:
            continue
        names = {row[1] for row in rows}
        if column not in names:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"))


_NEW_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_orders_user_created ON orders (user_id, created_at, id)",
    "CREATE INDEX IF NOT EXISTS ix_wallet_recharges_user_created ON wallet_recharges (user_id, created_at, id)",
    "CREATE INDEX IF NOT EXISTS ix_wallet_ledgers_user_id_id ON wallet_ledgers (user_id, id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_wallet_ledgers_ref ON wallet_ledgers (user_id, direction, ref_type, ref_id) "
    "WHERE ref_type IS NOT NULL AND ref_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_cards_claim ON cards (product_id, status, spec_id, id)",
    "CREATE INDEX IF NOT EXISTS ix_users_username ON users (username)",
)

_RETIRED_INDEXES = (
    "ix_wallet_recharges_user_id",
    "ix_wallet_ledgers_user_id",
    "ix_cards_product_id",
    "ix_cards_status",
    "ix_cards_product_spec_status",
)


def optimize_due(last: float, now: float, interval: float = 3600) -> bool:
    return now - last >= interval


def _optimize_raw(connection) -> None:
    raw = connection.connection.dbapi_connection
    cursor = raw.cursor()
    cursor.execute("PRAGMA optimize")
    cursor.execute("PRAGMA wal_checkpoint(PASSIVE)")
    cursor.close()


async def optimize(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.run_sync(_optimize_raw)


def _ensure_indexes(connection) -> None:
    tables = {
        row[0]
        for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'")).fetchall()
    }
    if "wallet_ledgers" in tables:
        duplicates = connection.execute(
            text(
                "SELECT user_id, direction, ref_type, ref_id, COUNT(*) "
                "FROM wallet_ledgers "
                "WHERE ref_type IS NOT NULL AND ref_id IS NOT NULL "
                "GROUP BY user_id, direction, ref_type, ref_id "
                "HAVING COUNT(*) > 1 "
                "LIMIT 5"
            )
        ).fetchall()
        if duplicates:
            raise RuntimeError(f"wallet_ledgers 存在重复入账，先处理后再启动: {duplicates}")
    for statement in _NEW_INDEXES:
        table = statement.split(" ON ", 1)[1].split(" ", 1)[0]
        if table not in tables:
            continue
        connection.execute(text(statement))
    for name in _RETIRED_INDEXES:
        connection.execute(text(f"DROP INDEX IF EXISTS {name}"))


def _allow_order_without_product(path) -> None:
    """旧库的 orders.product_id 非空。商品删除后要清空这个外键，订单仍留着。"""
    if not path.exists():
        return
    connection = sqlite3.connect(path)
    connection.isolation_level = None
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        columns = connection.execute("PRAGMA table_info(orders)").fetchall()
        if not columns:
            return
        product_id = next(column for column in columns if column[1] == "product_id")
        if product_id[3] == 0:
            return
        create_sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='orders'").fetchone()
        if create_sql is None or "product_id INTEGER NOT NULL" not in create_sql[0]:
            raise RuntimeError("orders.product_id 无法改为可空")
        indexes = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='orders' AND sql IS NOT NULL"
        ).fetchall()
        names = ", ".join(column[1] for column in columns)
        new_sql = create_sql[0].replace("CREATE TABLE orders", "CREATE TABLE orders__new", 1)
        new_sql = new_sql.replace("product_id INTEGER NOT NULL", "product_id INTEGER", 1)
        connection.execute("BEGIN")
        try:
            connection.execute(new_sql)
            connection.execute(f"INSERT INTO orders__new ({names}) SELECT {names} FROM orders")
            connection.execute("DROP TABLE orders")
            connection.execute("ALTER TABLE orders__new RENAME TO orders")
            for (index_sql,) in indexes:
                connection.execute(index_sql)
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()
