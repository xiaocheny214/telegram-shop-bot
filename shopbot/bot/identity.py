"""按 Telegram 用户找到商店用户。没有就创建。商家入口只放行管理员。"""

from collections import OrderedDict

from sqlalchemy.ext.asyncio import AsyncSession

from shopbot.bot.i18n import use_language
from shopbot.framework.config.app import get_app_settings
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import UserRole
from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.models import User
from shopbot.shop.user.service import create_user, get_user_by_telegram, set_user_role
from shopbot.system.access.service import ensure_access
from shopbot.system.audit.context import set_operator

_BUYER_LIMIT = 10_000
_buyer_ids: OrderedDict[int, int] = OrderedDict()
_languages: OrderedDict[int, str] = OrderedDict()


def cached_language(telegram_id: int) -> str | None:
    language = _languages.get(telegram_id)
    if language is None:
        return None
    _languages.move_to_end(telegram_id)
    return language


def remember_language(telegram_id: int, language: str) -> None:
    if language not in {"zh", "en"}:
        return
    _languages[telegram_id] = language
    _languages.move_to_end(telegram_id)
    while len(_languages) > _BUYER_LIMIT:
        _languages.popitem(last=False)
    use_language(language)


def bind_language(user: User) -> None:
    """用库里的语言。管理员缓存造出来的用户没有这一列时，沿用本进程已经记住的选择。"""
    stored = user.__dict__.get("language")
    if stored in {"zh", "en"}:
        remember_language(user.telegram_id, stored)
        return
    use_language(cached_language(user.telegram_id) or "zh")


def cached_buyer_id(telegram_id: int) -> int | None:
    user_id = _buyer_ids.get(telegram_id)
    if user_id is None:
        return None
    _buyer_ids.move_to_end(telegram_id)
    return user_id


async def open_buyer(session: AsyncSession, telegram_id: int, username: str | None) -> User:
    set_operator(telegram_id=telegram_id)
    user = await _find_or_create(session, telegram_id, username, int(UserRole.USER))
    if not user.is_active:
        _buyer_ids.pop(telegram_id, None)
        raise BizException("用户已停用", code=BizCode.FORBIDDEN)
    _buyer_ids[telegram_id] = user.id
    _buyer_ids.move_to_end(telegram_id)
    while len(_buyer_ids) > _BUYER_LIMIT:
        _buyer_ids.popitem(last=False)
    set_operator(user_id=user.id, telegram_id=user.telegram_id)
    bind_language(user)
    return user


async def open_merchant(session: AsyncSession, telegram_id: int, username: str | None) -> User:
    set_operator(telegram_id=telegram_id)
    configured = telegram_id in get_app_settings().admin_telegram_ids
    try:
        user = await get_user_by_telegram(session, telegram_id)
    except BizException as exc:
        if exc.message != "用户不存在":
            raise
        if not configured:
            raise BizException("无权访问", code=BizCode.FORBIDDEN)
        user = await create_user(
            session,
            {"telegram_id": telegram_id, "username": username, "role": int(UserRole.ADMIN)},
        )
    else:
        if configured and user.role != UserRole.ADMIN:
            user = await set_user_role(session, user.id, int(UserRole.ADMIN))
    set_operator(user_id=user.id, telegram_id=user.telegram_id)
    bind_language(user)
    return await ensure_access(session, telegram_id, (UserRole.ADMIN,))


async def _find_or_create(session: AsyncSession, telegram_id: int, username: str | None, role: int) -> User:
    try:
        return await get_user_by_telegram(session, telegram_id)
    except BizException as exc:
        if exc.message != "用户不存在":
            raise
        return await create_user(
            session,
            {"telegram_id": telegram_id, "username": username, "role": role},
        )
