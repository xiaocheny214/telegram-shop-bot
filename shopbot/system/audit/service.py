"""高危操作审计。

请求里只打印日志并把成功记录放进队列。满 50 条或超过 1 秒后批量写入，共用一次写锁。
401、403、500 立即落库。业务事务还开着时先暂存，等事务结束后再进入队列或落库。
失败原因只保留短句，不写卡密、密钥和回调报文。
"""

import asyncio
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException
from shopbot.system.audit.context import current_context
from shopbot.system.audit.schemas import AuditQuery, parse_audit_query
from shopbot.system.models import AuditLog

_RESULT_MAX = 255
_BATCH_SIZE = 50
_FLUSH_SECONDS = 1.0
_IMMEDIATE_STATUSES = {401, 403, 500}
_factory: async_sessionmaker[AsyncSession] | None = None
_pending_rows: ContextVar[list["_Entry"] | None] = ContextVar("audit_pending", default=None)
_queue: asyncio.Queue["_Entry | None"] | None = None
_writer: asyncio.Task[None] | None = None
_accepting = False
_logger = logging.getLogger("shopbot.audit")


@dataclass(frozen=True)
class AuditPage:
    """一页审计记录。has_more 为真时才需要请求下一页。"""

    items: list[AuditLog]
    page: int
    page_size: int
    has_more: bool


@dataclass(frozen=True)
class _Entry:
    action: str
    path: str | None
    client_ip: str | None
    operator_user_id: int | None
    operator_telegram_id: int | None
    status_code: int
    result: str
    duration_ms: int


def bind_audit_factory(factory: async_sessionmaker[AsyncSession]) -> None:
    global _factory
    _factory = factory


def start_audit_writer() -> asyncio.Task[None]:
    """启动批量写入。应用生命周期里调用一次。"""
    global _queue, _writer, _accepting
    if _writer is not None and not _writer.done():
        return _writer
    _accepting = True
    _queue = asyncio.Queue()
    _writer = asyncio.create_task(_run_writer())
    return _writer


async def stop_audit_writer() -> None:
    """停止接收新的批量记录，把队列里剩下的写完。"""
    global _accepting, _writer
    _accepting = False
    writer = _writer
    queue = _queue
    _writer = None
    if writer is None or queue is None:
        return
    await queue.put(None)
    await writer


def configure_audit_logging() -> None:
    """把审计打到标准错误。部署进程的标准错误即服务器日志。"""
    if _logger.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


def audit(action: str) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """包在事务装饰器外面。函数结束后再决定写入还是等到外层事务结束。"""

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
            except BizException as exc:
                await _capture(action, status_from_biz(exc), exc.message, started, _session(args, kwargs), exc)
                raise
            except Exception as exc:
                await _capture(action, 500, "内部错误", started, _session(args, kwargs), exc)
                raise
            await _capture(action, 200, "success", started, _session(args, kwargs), None)
            return result

        return wrapper

    return decorator


def status_from_biz(exc: BizException) -> int:
    """与异常处理器返回的 HTTP 状态一致。"""
    if exc.code == BizCode.UNAUTHORIZED:
        return 401
    if exc.code == BizCode.FORBIDDEN:
        return 403
    if exc.code == BizCode.INTERNAL_ERROR:
        return 500
    return 400


async def capture(
    *,
    action: str,
    status_code: int,
    result: str,
    started: float,
    session: AsyncSession | None,
) -> None:
    await _capture(action, status_code, result, started, session, None)


async def list_audits(session: AsyncSession, payload: AuditQuery | dict[str, Any]) -> AuditPage:
    """按条件分页查询。最多返回一页，不统计全表行数。"""
    query = parse_audit_query(payload)
    statement = select(AuditLog)
    if query.created_from is not None:
        statement = statement.where(AuditLog.created_at >= query.created_from)
    if query.created_to is not None:
        statement = statement.where(AuditLog.created_at <= query.created_to)
    if query.operator_user_id is not None:
        statement = statement.where(AuditLog.operator_user_id == query.operator_user_id)
    if query.operator_telegram_id is not None:
        statement = statement.where(AuditLog.operator_telegram_id == query.operator_telegram_id)
    if query.action is not None:
        statement = statement.where(AuditLog.action == query.action)
    if query.status_code is not None:
        statement = statement.where(AuditLog.status_code == query.status_code)
    statement = statement.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    offset = (query.page - 1) * query.page_size
    result = await session.scalars(statement.offset(offset).limit(query.page_size + 1))
    rows = list(result.all())
    has_more = len(rows) > query.page_size
    return AuditPage(
        items=rows[: query.page_size],
        page=query.page,
        page_size=query.page_size,
        has_more=has_more,
    )


async def flush_audits() -> None:
    """请求事务关闭后调用。成功记录进入批量队列，严重失败立即落库。"""
    for row in _take_pending():
        await _submit(row)


async def _capture(
    action: str,
    status_code: int,
    result: str,
    started: float,
    session: AsyncSession | None,
    error: Exception | None,
) -> None:
    context = current_context()
    entry = _Entry(
        action=action[:128],
        path=context.path,
        client_ip=context.client_ip,
        operator_user_id=context.operator_user_id,
        operator_telegram_id=context.operator_telegram_id,
        status_code=status_code,
        result=_short(result),
        duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
    )
    _emit(entry)
    try:
        if _transaction_open(session):
            _stash(entry)
            return
        await _submit(entry)
    except Exception:
        if error is not None:
            return
        raise


def _stash(entry: _Entry) -> None:
    rows = _pending_rows.get()
    if rows is None:
        rows = []
        _pending_rows.set(rows)
    rows.append(entry)


def _take_pending() -> list[_Entry]:
    rows = _pending_rows.get()
    if not rows:
        return []
    copied = list(rows)
    rows.clear()
    return copied


async def _run_writer() -> None:
    queue = _queue
    if queue is None:
        return
    batch: list[_Entry] = []
    deadline: float | None = None
    while True:
        timeout = None if deadline is None else max(0.0, deadline - asyncio.get_running_loop().time())
        try:
            if timeout is None:
                item = await queue.get()
            else:
                item = await asyncio.wait_for(queue.get(), timeout)
        except TimeoutError:
            await _insert(batch)
            batch = []
            deadline = None
            continue
        if item is None:
            if batch:
                await _insert(batch)
            return
        if not batch:
            deadline = asyncio.get_running_loop().time() + _FLUSH_SECONDS
        batch.append(item)
        if len(batch) >= _BATCH_SIZE:
            await _insert(batch)
            batch = []
            deadline = None


async def _submit(row: _Entry) -> None:
    if row.status_code in _IMMEDIATE_STATUSES or not _accepting or _queue is None:
        await _insert([row])
        return
    _queue.put_nowait(row)


async def _insert(rows: list[_Entry]) -> None:
    if not rows or _factory is None:
        return
    try:
        await _insert_rows(rows)
    except Exception:
        _logger.exception("audit batch write failed")


async def _insert_rows(rows: list[_Entry]) -> None:
    if _factory is None:
        return
    async with _factory() as session:
        async with session.begin():
            session.add_all(
                [
                    AuditLog(
                        operator_user_id=row.operator_user_id,
                        operator_telegram_id=row.operator_telegram_id,
                        action=row.action,
                        path=row.path,
                        status_code=row.status_code,
                        result=row.result,
                        duration_ms=row.duration_ms,
                        client_ip=row.client_ip,
                    )
                    for row in rows
                ]
            )


def _transaction_open(session: AsyncSession | None) -> bool:
    if session is None:
        return False
    transaction = session.sync_session.get_transaction()
    return transaction is not None and transaction.is_active


def _session(args: tuple[Any, ...], kwargs: dict[str, Any]) -> AsyncSession | None:
    session = kwargs.get("session")
    if isinstance(session, AsyncSession):
        return session
    for arg in args:
        if isinstance(arg, AsyncSession):
            return arg
    return None


def _emit(row: _Entry) -> None:
    message = (
        "audit action={action} path={path} status={status} result={result} "
        "duration_ms={duration} ip={ip} operator_user={user_id} operator_telegram={telegram_id}"
    ).format(
        action=row.action,
        path=_field(row.path),
        status=row.status_code,
        result=row.result,
        duration=row.duration_ms,
        ip=_field(row.client_ip),
        user_id=_field(None if row.operator_user_id is None else str(row.operator_user_id)),
        telegram_id=_field(None if row.operator_telegram_id is None else str(row.operator_telegram_id)),
    )
    if row.status_code >= 500:
        _logger.error(message)
    elif row.status_code >= 400:
        _logger.warning(message)
    else:
        _logger.info(message)


def _field(value: str | None) -> str:
    return "-" if not value else value


def _short(result: str) -> str:
    text = " ".join(result.split())
    if not text:
        return "success"
    return text[:_RESULT_MAX]
