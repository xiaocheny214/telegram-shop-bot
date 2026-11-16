"""用户的创建、查询、资料聚合、停用和删除。

入参格式由 Pydantic 校验。写入和查询使用 ORM 绑定参数，不把输入拼进 SQL。
创建、停用、删除在事务中执行。FastAPI 已开启事务时并入该事务。
余额由钱包流水维护，创建用户时不接收余额。
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shopbot.framework.db import transactional
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import UserRole
from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.models import Order, User, WalletLedger
from shopbot.shop.user.schemas import UserCreate, UserProfile, parse_model, parse_user_id
from shopbot.system.audit.service import audit


@audit("user.create")
@transactional
async def create_user(session: AsyncSession, payload: UserCreate | dict) -> User:
    data = parse_model(UserCreate, payload)
    if await session.scalar(select(User.id).where(User.telegram_id == data.telegram_id)):
        raise BizException("用户已存在", code=BizCode.CONFLICT)
    user = User(telegram_id=data.telegram_id, username=data.username, role=data.role, is_active=True)
    session.add(user)
    await session.flush()
    return user


async def get_user(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, parse_user_id(user_id))
    if user is None:
        raise BizException("用户不存在", code=BizCode.NOT_FOUND)
    return user


async def get_user_profile(session: AsyncSession, user_id: int) -> UserProfile:
    """给买家资料页用。用户不存在时沿用 get_user 的错误。订单数包含全部状态。"""
    user = await get_user(session, user_id)
    order_count = await session.scalar(select(func.count()).select_from(Order).where(Order.user_id == user.id))
    return UserProfile(
        user_id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        balance_usdt_cents=user.balance_usdt_cents,
        order_count=int(order_count or 0),
        created_at=user.created_at,
    )


async def get_user_by_telegram(session: AsyncSession, telegram_id: int) -> User:
    if type(telegram_id) is not int or telegram_id <= 0:
        raise BizException("Telegram 编号无效", code=BizCode.BAD_REQUEST)
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        raise BizException("用户不存在", code=BizCode.NOT_FOUND)
    return user


async def list_users(session: AsyncSession, *, active_only: bool = False) -> list[User]:
    statement = select(User).order_by(User.id)
    if active_only:
        statement = statement.where(User.is_active.is_(True))
    result = await session.scalars(statement.limit(200))
    return list(result.all())


@audit("user.role")
@transactional
async def set_user_role(session: AsyncSession, user_id: int, role: int) -> User:
    """把用户改成指定角色。商家机器人用来把配置里的管理员升权。"""
    if type(role) is not int or role not in UserRole._value2member_map_:
        raise BizException("用户角色无效", code=BizCode.BAD_REQUEST)
    user = await get_user(session, user_id)
    user.role = role
    await session.flush()
    return user


@transactional
async def set_user_language(session: AsyncSession, user_id: int, language: str) -> User:
    """买家和商家机器人共用这一项。只接受中文或英文。"""
    if language not in {"zh", "en"}:
        raise BizException("语言无效", code=BizCode.BAD_REQUEST)
    user = await get_user(session, user_id)
    user.language = language
    await session.flush()
    return user


@audit("user.deactivate")
@transactional
async def deactivate_user(session: AsyncSession, user_id: int) -> User:
    user = await get_user(session, user_id)
    if not user.is_active:
        raise BizException("用户已停用", code=BizCode.CONFLICT)
    user.is_active = False
    await session.flush()
    return user


@audit("user.delete")
@transactional
async def delete_user(session: AsyncSession, user_id: int) -> None:
    user = await get_user(session, user_id)
    if user.balance_usdt_cents != 0:
        raise BizException("用户余额不为零，不能删除", code=BizCode.CONFLICT)
    order_count = await session.scalar(select(func.count()).select_from(Order).where(Order.user_id == user.id))
    if order_count:
        raise BizException("用户有订单，不能删除", code=BizCode.CONFLICT)
    ledger_count = await session.scalar(
        select(func.count()).select_from(WalletLedger).where(WalletLedger.user_id == user.id)
    )
    if ledger_count:
        raise BizException("用户有余额流水，不能删除", code=BizCode.CONFLICT)
    await session.delete(user)
    await session.flush()
