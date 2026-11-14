"""定时任务启动器。

启动时把已注册的任务补进 scheduled_tasks，已有配置不覆盖。
每一轮读取管理员设置的间隔和启停，到点才调用对应函数。
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shopbot.framework.exceptions.biz import BizException
from shopbot.system.models import ScheduledTask

Handler = Callable[[async_sessionmaker[AsyncSession]], Awaitable[object]]
_IDLE_SECONDS = 5.0


@dataclass(frozen=True)
class TaskSpec:
    code: str
    name: str
    interval_seconds: int


@dataclass(frozen=True)
class _TaskView:
    code: str
    interval_seconds: int
    is_active: bool
    last_run_at: datetime | None


_SPECS: dict[str, TaskSpec] = {}
_HANDLERS: dict[str, Handler] = {}


def register(spec: TaskSpec, handler: Handler) -> None:
    _SPECS[spec.code] = spec
    _HANDLERS[spec.code] = handler


async def run_scheduler(factory: async_sessionmaker[AsyncSession]) -> None:
    while True:
        try:
            delay = await run_due(factory)
        except asyncio.CancelledError:
            raise
        except Exception:
            delay = _IDLE_SECONDS
        await asyncio.sleep(delay)


async def run_due(factory: async_sessionmaker[AsyncSession]) -> float:
    """执行到期任务，返回距离下一轮检查的秒数。"""
    async with factory() as session:
        async with session.begin():
            await _ensure_registered(session)
            views = await _load_views(session)
    now = datetime.now(timezone.utc)
    for task in views:
        if _is_due(task, now):
            error = await _invoke(task.code, factory)
            await _mark_ran(factory, task.code, error)
    async with factory() as session:
        views = await _load_views(session)
    return _next_delay(views, datetime.now(timezone.utc))


async def _ensure_registered(session: AsyncSession) -> None:
    for spec in _SPECS.values():
        existing = await session.scalar(select(ScheduledTask.id).where(ScheduledTask.code == spec.code))
        if existing is None:
            session.add(
                ScheduledTask(
                    code=spec.code,
                    name=spec.name,
                    interval_seconds=spec.interval_seconds,
                    is_active=True,
                )
            )
    await session.flush()


async def _load_views(session: AsyncSession) -> list[_TaskView]:
    rows = list(await session.scalars(select(ScheduledTask).order_by(ScheduledTask.id)))
    return [
        _TaskView(
            code=row.code,
            interval_seconds=row.interval_seconds,
            is_active=row.is_active,
            last_run_at=row.last_run_at,
        )
        for row in rows
    ]


async def _invoke(code: str, factory: async_sessionmaker[AsyncSession]) -> str | None:
    handler = _HANDLERS.get(code)
    if handler is None:
        return None
    try:
        await handler(factory)
    except asyncio.CancelledError:
        raise
    except BizException as exc:
        return exc.message[:255]
    except Exception:
        return "任务执行失败"
    return None


async def _mark_ran(factory: async_sessionmaker[AsyncSession], code: str, error: str | None) -> None:
    async with factory() as session:
        async with session.begin():
            task = await session.scalar(select(ScheduledTask).where(ScheduledTask.code == code))
            if task is None:
                return
            task.last_run_at = datetime.now(timezone.utc)
            task.last_error = error


def _is_due(task: _TaskView, now: datetime) -> bool:
    if not task.is_active or task.code not in _HANDLERS:
        return False
    if task.last_run_at is None:
        return True
    return _as_utc(task.last_run_at) + timedelta(seconds=task.interval_seconds) <= now


def _next_delay(tasks: list[_TaskView], now: datetime) -> float:
    delays: list[float] = []
    for task in tasks:
        if not task.is_active or task.code not in _HANDLERS:
            continue
        if task.last_run_at is None:
            delays.append(1.0)
            continue
        elapsed = (now - _as_utc(task.last_run_at)).total_seconds()
        delays.append(max(1.0, task.interval_seconds - elapsed))
    if not delays:
        return _IDLE_SECONDS
    return min(delays)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
