"""余额和流水的读写都从这里进入。

余额记在用户表，单位是 USDT 分。每一笔变动同时写一条流水。
同一事务里先按条件更新余额，再写入流水。余额不足或超过上限时整笔回滚。
带关联编号的流水只能记一次，避免同一笔业务重复入账或扣款。
充值走支付渠道，回调确认后入账。购买商品从余额扣款。
渠道请求在写事务之外，由支付线程池执行。失败则关闭充值单。渠道已经建单时一并关闭远程订单。
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shopbot.framework.admission import prepare_user_lookup, remember_missing_user, write_slot
from shopbot.framework.db import transactional
from shopbot.framework.executor import run_blocking
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import Currency, OrderStatus, PaymentProvider, WalletDirection
from shopbot.framework.exceptions.biz import BizException
from shopbot.framework.utils.snowflake import SnowflakeError, next_id
from shopbot.shop.models import PaymentNotice, ShopSetting, User, WalletLedger, WalletRecharge
from shopbot.shop.user.service import get_user
from shopbot.shop.wallet.schemas import RechargeCreate, RechargePageQuery, WalletChange, parse_model
from shopbot.system.payment.confirm import VerifiedPayment
from shopbot.system.payment.drivers import close_remote_payment, copy_payment_party, open_payment, payment_timeout_seconds
from shopbot.system.payment.gateway import ChannelPayment
from shopbot.system.payment.service import get_payment_channel
from shopbot.system.vault import protect, reveal, show

logger = logging.getLogger(__name__)
_RECHARGE_TIMEOUT_SECONDS = 900
_RECHARGE_PENDING_LIMIT = 3

_BALANCE_MAX = 10**15
_LIST_MAX = 100


@transactional
async def credit_balance(session: AsyncSession, payload: WalletChange | dict) -> tuple[User, WalletLedger]:
    """增加余额，并写一条入账流水。"""
    return await _change(session, payload, WalletDirection.CREDIT)


@transactional
async def debit_balance(session: AsyncSession, payload: WalletChange | dict) -> tuple[User, WalletLedger]:
    """减少余额，并写一条出账流水。余额不足时拒绝。"""
    return await _change(session, payload, WalletDirection.DEBIT)


async def get_balance(session: AsyncSession, user_id: int) -> int:
    user = await get_user(session, user_id)
    return user.balance_usdt_cents


@dataclass(frozen=True)
class RechargePage:
    items: list[WalletRecharge]
    page: int
    page_size: int
    has_more: bool


async def list_user_recharges(session: AsyncSession, payload: RechargePageQuery | dict) -> RechargePage:
    """按用户分页列出充值单。"""
    data = parse_model(RechargePageQuery, payload)
    await get_user(session, data.user_id)
    offset = (data.page - 1) * data.page_size
    rows = list(
        await session.scalars(
            select(WalletRecharge)
            .where(WalletRecharge.user_id == data.user_id)
            .order_by(WalletRecharge.created_at.desc(), WalletRecharge.id.desc())
            .offset(offset)
            .limit(data.page_size + 1)
        )
    )
    has_more = len(rows) > data.page_size
    items = rows[: data.page_size]
    for item in items:
        show(item, "pay_target", reveal(item.pay_target))
        show(item, "pay_url", reveal(item.pay_url))
    return RechargePage(
        items=items,
        page=data.page,
        page_size=data.page_size,
        has_more=has_more,
    )


async def list_ledgers(session: AsyncSession, user_id: int) -> list[WalletLedger]:
    await get_user(session, user_id)
    result = await session.scalars(
        select(WalletLedger)
        .where(WalletLedger.user_id == user_id)
        .order_by(WalletLedger.id.desc())
        .limit(_LIST_MAX)
    )
    return list(result.all())


async def _change(
    session: AsyncSession,
    payload: WalletChange | dict,
    direction: WalletDirection,
) -> tuple[User, WalletLedger]:
    data = parse_model(WalletChange, payload)
    user = await get_user(session, data.user_id)
    await _reject_duplicate(session, user.id, direction, data.ref_type, data.ref_id)
    if direction is WalletDirection.CREDIT:
        changed = await _credit(session, user.id, data.amount_usdt_cents)
        if not changed:
            raise BizException("余额超出上限", code=BizCode.CONFLICT)
    else:
        changed = await _debit(session, user.id, data.amount_usdt_cents)
        if not changed:
            raise BizException("余额不足", code=BizCode.CONFLICT)
    ledger = WalletLedger(
        user_id=user.id,
        amount_usdt_cents=data.amount_usdt_cents,
        direction=direction,
        reason=data.reason,
        ref_type=data.ref_type,
        ref_id=data.ref_id,
    )
    session.add(ledger)
    await session.flush()
    await session.refresh(user)
    return user, ledger


async def _credit(session: AsyncSession, user_id: int, amount: int) -> bool:
    result = await session.execute(
        update(User)
        .where(User.id == user_id, User.balance_usdt_cents + amount <= _BALANCE_MAX)
        .values(balance_usdt_cents=User.balance_usdt_cents + amount)
    )
    return result.rowcount == 1


async def _debit(session: AsyncSession, user_id: int, amount: int) -> bool:
    result = await session.execute(
        update(User)
        .where(User.id == user_id, User.balance_usdt_cents >= amount)
        .values(balance_usdt_cents=User.balance_usdt_cents - amount)
    )
    return result.rowcount == 1


def to_usdt_cents(amount_cents: int, currency: int, rates: dict[int, int]) -> int:
    """把商品金额折成要扣的 USDT 分。USDT 商品不折算。"""
    if currency == Currency.USDT:
        return amount_cents
    usdt_rate = rates.get(Currency.USDT, 0)
    if type(usdt_rate) is not int or usdt_rate <= 0:
        raise BizException("未配置USDT汇率", code=BizCode.BAD_REQUEST)
    if currency == Currency.CNY:
        converted = (amount_cents * 100 + usdt_rate // 2) // usdt_rate
    else:
        rate = rates.get(currency, 0)
        label = {Currency.USD: "美元", Currency.GBP: "英镑", Currency.EUR: "欧元"}.get(Currency(currency)) if currency in Currency._value2member_map_ else None
        if label is None or type(rate) is not int or rate <= 0:
            raise BizException("未配置汇率" if label is None else f"未配置{label}汇率", code=BizCode.BAD_REQUEST)
        converted = (amount_cents * rate + usdt_rate // 2) // usdt_rate
    if converted < 1:
        raise BizException("折算后金额过小", code=BizCode.BAD_REQUEST)
    return converted


@dataclass(frozen=True)
class _RechargeRequest:
    recharge_id: int
    recharge_no: str
    amount_usdt_cents: int
    network: int | None
    timeout_seconds: int
    cny_rates: dict[int, int]
    channel: object
    detail: object


async def recharge_wallet(factory: async_sessionmaker[AsyncSession], payload: RechargeCreate | dict) -> WalletRecharge:
    """创建充值单并向渠道要收银台。到账金额在回调确认后入账。渠道失败则关闭充值单。收银台没记下时关闭远程订单。"""
    data = parse_model(RechargeCreate, payload)
    prepare_user_lookup(data.user_id)
    async with write_slot():
        pending = await _create_recharge(factory, data)
    payment = None
    try:
        payment = await run_blocking(
            open_payment,
            pending.channel,
            pending.detail,
            order_no=pending.recharge_no,
            subject="钱包充值",
            amount_cents=pending.amount_usdt_cents,
            currency=Currency.USDT,
            network=pending.network,
            timeout_seconds=pending.timeout_seconds,
            cny_rates=pending.cny_rates,
        )
        return await _store_recharge_payment(factory, pending.recharge_id, payment)
    except Exception:
        try:
            await _abort_recharge(factory, pending.recharge_id)
        except Exception:
            logger.exception("关闭本地充值单失败 recharge_id=%s", pending.recharge_id)
        if payment is not None:
            try:
                await run_blocking(close_remote_payment, pending.channel, pending.detail, payment.channel_trade_no)
            except Exception:
                logger.exception("关闭远程支付订单失败 recharge_no=%s", pending.recharge_no)
        raise


async def _create_recharge(factory: async_sessionmaker[AsyncSession], data: RechargeCreate) -> _RechargeRequest:
    async with factory() as session:
        try:
            return await _insert_recharge(session, data)
        except BizException as exc:
            if exc.message == "用户不存在":
                remember_missing_user(data.user_id)
            raise


@transactional
async def _insert_recharge(session: AsyncSession, data: RechargeCreate) -> _RechargeRequest:
    user = await get_user(session, data.user_id)
    if not user.is_active:
        raise BizException("用户不可用", code=BizCode.CONFLICT)
    channel, detail = await get_payment_channel(session, data.channel_id)
    if not channel.is_active:
        raise BizException("支付渠道未启用", code=BizCode.CONFLICT)
    setting = await _shop_setting(session)
    now = datetime.now(timezone.utc)
    await _reject_too_many_recharges(session, user.id, setting.max_pending_orders, now)
    try:
        recharge_id = next_id()
    except SnowflakeError as exc:
        raise BizException("订单号生成失败", code=BizCode.CONFLICT) from exc
    network = data.network if channel.provider == PaymentProvider.BEPUSDT else None
    timeout_seconds = payment_timeout_seconds(channel.provider, setting.order_timeout_seconds)
    recharge = WalletRecharge(
        id=recharge_id,
        recharge_no=str(recharge_id),
        user_id=user.id,
        channel_id=channel.id,
        pay_provider=channel.provider,
        pay_network=network,
        amount_usdt_cents=data.amount_usdt_cents,
        status=OrderStatus.PENDING,
        expires_at=now + timedelta(seconds=timeout_seconds or _RECHARGE_TIMEOUT_SECONDS),
    )
    session.add(recharge)
    await session.flush()
    copied_channel, copied_detail = copy_payment_party(channel, detail)
    return _RechargeRequest(
        recharge_id=recharge.id,
        recharge_no=recharge.recharge_no,
        amount_usdt_cents=recharge.amount_usdt_cents,
        network=network,
        timeout_seconds=timeout_seconds,
        cny_rates={
            Currency.USD: setting.usd_cny_cents,
            Currency.USDT: setting.usdt_cny_cents,
            Currency.GBP: setting.gbp_cny_cents,
            Currency.EUR: setting.eur_cny_cents,
        },
        channel=copied_channel,
        detail=copied_detail,
    )


async def _store_recharge_payment(
    factory: async_sessionmaker[AsyncSession],
    recharge_id: int,
    payment: ChannelPayment,
) -> WalletRecharge:
    async with factory() as session:
        async with session.begin():
            recharge = await session.get(WalletRecharge, recharge_id)
            if recharge is None or recharge.status != OrderStatus.PENDING:
                raise BizException("充值单不在待支付", code=BizCode.CONFLICT)
            recharge.channel_trade_no = payment.channel_trade_no or None
            recharge.pay_amount = payment.pay_amount
            recharge.pay_target = protect(payment.pay_target)
            recharge.pay_url = protect(payment.pay_url)
            await session.flush()
            session.expunge(recharge)
        recharge.pay_target = reveal(recharge.pay_target)
        recharge.pay_url = reveal(recharge.pay_url)
        return recharge


async def _abort_recharge(factory: async_sessionmaker[AsyncSession], recharge_id: int) -> None:
    async with factory() as session:
        async with session.begin():
            recharge = await session.get(WalletRecharge, recharge_id)
            if recharge is None or recharge.status != OrderStatus.PENDING:
                return
            recharge.status = OrderStatus.CANCELLED


async def settle_recharge(
    session: AsyncSession,
    channel_id: int,
    provider: int,
    verified: VerifiedPayment,
    payload: str,
) -> None:
    """渠道回调确认后，把充值金额记入钱包。同一充值单只入账一次。"""
    recharge = await session.scalar(select(WalletRecharge).where(WalletRecharge.recharge_no == verified.order_no))
    if recharge is None:
        raise BizException("订单不存在", code=BizCode.NOT_FOUND)
    if recharge.channel_id != channel_id or recharge.pay_provider != provider:
        raise BizException("支付渠道不符", code=BizCode.CONFLICT)
    if not _same_money(verified.paid_amount, recharge.pay_amount):
        raise BizException("支付金额不符", code=BizCode.CONFLICT)
    if verified.fiat_amount is not None and not _same_money(verified.fiat_amount, _yuan_text(recharge.amount_usdt_cents)):
        raise BizException("支付金额不符", code=BizCode.CONFLICT)
    await _save_notice(session, verified, payload)
    if recharge.status == OrderStatus.PAID:
        return
    if recharge.status not in (OrderStatus.PENDING, OrderStatus.EXPIRED):
        raise BizException("充值单不在待支付", code=BizCode.CONFLICT)
    result = await session.execute(
        update(WalletRecharge)
        .where(WalletRecharge.id == recharge.id, WalletRecharge.status == recharge.status)
        .values(status=OrderStatus.PAID, paid_at=datetime.now(timezone.utc))
    )
    if result.rowcount != 1:
        raise BizException("充值单状态已变化", code=BizCode.CONFLICT)
    await credit_balance(
        session,
        {
            "user_id": recharge.user_id,
            "amount_usdt_cents": recharge.amount_usdt_cents,
            "reason": "充值",
            "ref_type": "recharge",
            "ref_id": recharge.recharge_no,
        },
    )


async def close_expired_recharges(factory: async_sessionmaker[AsyncSession]) -> int:
    """关闭已过期且未支付的充值单。未入账，不改余额。"""
    now = datetime.now(timezone.utc)
    async with factory() as session:
        recharge_ids = list(
            await session.scalars(
                select(WalletRecharge.id)
                .where(
                    WalletRecharge.status == OrderStatus.PENDING,
                    WalletRecharge.expires_at.is_not(None),
                    WalletRecharge.expires_at <= now,
                )
                .order_by(WalletRecharge.expires_at)
                .limit(100)
            )
        )
    closed = 0
    for recharge_id in recharge_ids:
        async with factory() as session:
            async with session.begin():
                recharge = await session.get(WalletRecharge, recharge_id)
                if recharge is None or recharge.status != OrderStatus.PENDING:
                    continue
                if recharge.expires_at is None or _as_utc(recharge.expires_at) > datetime.now(timezone.utc):
                    continue
                recharge.status = OrderStatus.EXPIRED
                closed += 1
    return closed


async def _reject_too_many_recharges(session: AsyncSession, user_id: int, limit: int, now: datetime) -> None:
    online_count = await session.scalar(
        select(func.count())
        .select_from(WalletRecharge)
        .where(
            WalletRecharge.user_id == user_id,
            WalletRecharge.status == OrderStatus.PENDING,
            WalletRecharge.expires_at > now,
        )
    )
    if online_count is not None and online_count >= limit:
        raise BizException("请关闭充值", code=BizCode.CONFLICT)


async def _shop_setting(session: AsyncSession) -> ShopSetting:
    setting = await session.get(ShopSetting, 1)
    if setting is not None:
        return setting
    setting = ShopSetting(id=1, order_timeout_seconds=_RECHARGE_TIMEOUT_SECONDS, max_pending_orders=_RECHARGE_PENDING_LIMIT)
    session.add(setting)
    await session.flush()
    return setting


async def _save_notice(session: AsyncSession, verified: VerifiedPayment, payload: str) -> None:
    from sqlalchemy.exc import IntegrityError

    try:
        async with session.begin_nested():
            session.add(
                PaymentNotice(
                    order_no=verified.order_no,
                    channel_trade_no=verified.channel_trade_no,
                    channel_status=verified.channel_status,
                    payload=protect(payload) or "",
                )
            )
            await session.flush()
    except IntegrityError:
        return


def _same_money(left: object, right: object) -> bool:
    try:
        return Decimal(str(left)).normalize() == Decimal(str(right)).normalize()
    except Exception:
        return False


def _yuan_text(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _reject_duplicate(
    session: AsyncSession,
    user_id: int,
    direction: WalletDirection,
    ref_type: str | None,
    ref_id: str | None,
) -> None:
    if ref_type is None or ref_id is None:
        return
    existing = await session.scalar(
        select(WalletLedger.id).where(
            WalletLedger.user_id == user_id,
            WalletLedger.direction == direction,
            WalletLedger.ref_type == ref_type,
            WalletLedger.ref_id == ref_id,
        )
    )
    if existing is not None:
        raise BizException("流水已存在", code=BizCode.CONFLICT)
