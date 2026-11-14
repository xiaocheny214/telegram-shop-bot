"""按用户角色决定能不能进接口。角色写在函数注解上，不另建权限表。"""

from sqlalchemy.ext.asyncio import AsyncSession

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import UserRole
from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.models import User
from shopbot.shop.user.service import get_user_by_telegram
from shopbot.system.access.cache import AdminIdentity, lookup_admin, remember_admin


async def ensure_access(
    session: AsyncSession,
    telegram_id: int,
    roles: tuple[UserRole, ...],
) -> User:
    """注解必须写明角色。有效管理员命中缓存后不再查库。"""
    if not roles or any(type(role) is not UserRole for role in roles):
        raise BizException("未声明可访问角色", code=BizCode.FORBIDDEN)
    generation, cached = lookup_admin(telegram_id)
    if cached is not None:
        return _grant(_user_from_cache(cached), roles)
    user = await get_user_by_telegram(session, telegram_id)
    if user.is_active and user.role == UserRole.ADMIN:
        remember_admin(
            telegram_id,
            generation,
            AdminIdentity(user_id=user.id, telegram_id=user.telegram_id, role=user.role),
        )
    return _grant(user, roles)


def _user_from_cache(identity: AdminIdentity) -> User:
    """缓存里只有编号和角色，余额不在这里。"""
    return User(
        id=identity.user_id,
        telegram_id=identity.telegram_id,
        role=identity.role,
        is_active=True,
    )


def _grant(user: User, roles: tuple[UserRole, ...]) -> User:
    if not user.is_active:
        raise BizException("用户已停用", code=BizCode.FORBIDDEN)
    if user.role not in roles:
        raise BizException("无权访问", code=BizCode.FORBIDDEN)
    return user
