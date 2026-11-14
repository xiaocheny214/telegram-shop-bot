"""按 DatabaseConfig 创建引擎和会话。表声明在 shop.models。"""

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, SessionTransactionOrigin

from shopbot.framework.config.database import DatabaseConfig


class Base(DeclarativeBase):
    pass


def create_engine(config: DatabaseConfig) -> AsyncEngine:
    engine = create_async_engine(config.url, echo=config.echo)
    timeout_ms = config.busy_timeout_ms

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        # 关掉驱动自己的 BEGIN。事务由下面的事件发出 BEGIN IMMEDIATE，一进来就拿写锁。
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        if config.wal:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={timeout_ms}")
        cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def _begin_immediate(connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def ensure_transaction(session: AsyncSession):
    """没有显式事务时开启并在成功后提交，失败时回滚。

    查询会让会话自动开始事务，这种不算外层事务，写完后由这里提交。
    调用方已经 begin 时，提交和回滚交给外层。
    SQLite 事务都是 BEGIN IMMEDIATE，开始时就排队拿写锁。
    """
    transaction = session.sync_session.get_transaction()
    if transaction is not None and transaction.origin in {
        SessionTransactionOrigin.BEGIN,
        SessionTransactionOrigin.BEGIN_NESTED,
    }:
        yield
        return
    if transaction is not None and transaction.origin is SessionTransactionOrigin.AUTOBEGIN:
        try:
            yield
        except Exception:
            await session.rollback()
            raise
        await session.commit()
        return
    async with session.begin():
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

    engine = create_engine(config)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine
