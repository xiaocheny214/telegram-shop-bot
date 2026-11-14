"""接口注解。HTTP 只读取注解，Telegram 在进函数前先判断角色。"""

import inspect
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import UserRole
from shopbot.framework.exceptions.biz import BizException
from shopbot.system.access.service import ensure_access
from shopbot.system.audit.context import set_operator
from shopbot.system.audit.service import capture, status_from_biz

ACCESS_ROLES = "__access_roles__"


def require(*roles: UserRole) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """把允许的角色写到函数上。角色必填。带 session 和 telegram_id 的函数会先查角色再执行。"""
    if not roles or any(type(role) is not UserRole for role in roles):
        raise BizException("未声明可访问角色", code=BizCode.FORBIDDEN)
    allowed = tuple(roles)

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        setattr(func, ACCESS_ROLES, allowed)
        if not _accepts_telegram_call(func):
            return func

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = inspect.signature(func).bind_partial(*args, **kwargs)
            await ensure_access(bound.arguments["session"], bound.arguments["telegram_id"], allowed)
            return await func(*args, **kwargs)

        setattr(wrapper, ACCESS_ROLES, allowed)
        return wrapper

    return decorator


def _accepts_telegram_call(func: Callable[..., Any]) -> bool:
    names = inspect.signature(func).parameters
    return "session" in names and "telegram_id" in names


async def current_telegram_id() -> int:
    """已确认的 Telegram 编号。登录尚未接入，标了注解的 HTTP 接口一律未登录。"""
    raise BizException("未登录", code=BizCode.UNAUTHORIZED)


async def enforce_access(request: Request, session: AsyncSession) -> None:
    """管理路由的依赖。没有角色注解的接口放行，有注解才核对用户。拒绝会留下审计。"""
    endpoint = request.scope.get("endpoint")
    if endpoint is None or not hasattr(endpoint, ACCESS_ROLES):
        return
    started = time.perf_counter()
    try:
        roles = getattr(endpoint, ACCESS_ROLES)
        if not isinstance(roles, tuple) or not roles:
            raise BizException("未声明可访问角色", code=BizCode.FORBIDDEN)
        telegram_id = await current_telegram_id()
        set_operator(telegram_id=telegram_id)
        user = await ensure_access(session, telegram_id, roles)
        set_operator(user_id=user.id, telegram_id=user.telegram_id)
    except BizException as exc:
        try:
            await capture(
                action=request.url.path,
                status_code=status_from_biz(exc),
                result=exc.message,
                started=started,
                session=session,
            )
        finally:
            raise exc
