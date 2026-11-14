"""下单、支付和关闭。

在线待支付订单按用户、状态、过期时间查询，走 ix_orders_user_pending。
在线待支付订单达到店铺上限时拒绝。上限读商家配置，初始为 3。配置为 3 时，第 4 笔拒绝。
不存在的用户和超额的用户查询在打开数据库前拒绝。渠道请求在写事务之外，由支付线程池执行。渠道已经建单、本地却失败回滚时，关闭远程订单。
扣库存由商品上的策略决定。日常策略按购买数量下单即占用，营销策略固定一张并在支付成功后占用。
下单时读取已配置的支付渠道和购买数量，把收银台地址写入订单详情。状态写成待支付。
易支付、支付宝、微信按店铺汇率把商品金额折成人民币后再交给渠道。
渠道支付只认异步回调。先验签，再向渠道查单，订单号和金额与本地一致才把卡密标为已售。同一订单只发一次。
钱包充值确认后入账。选择商品并使用钱包付款时，立刻按汇率扣 USDT 余额，再占用卡密并发货。
自动发货的商品接着把订单号、商品名称、商品详情、卡密和感谢话语发给买家。手动发货留给管理员发送。
未支付订单过了 expires_at 后关闭。日常策略把占用的卡密退回库存，营销策略下单时没有占用库存。
关单和支付都使用下单时记下的库存策略。订单已过期或已取消后，渠道仍确认付款的，重新占用卡密并发货。库存不够时不向渠道返回成功。
到期扫描由系统定时任务 close_expired_orders 调用，间隔和启停在 scheduled_tasks。
写入在事务中执行。FastAPI 已开启事务时并入该事务。
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shopbot.framework.admission import prepare_user_lookup, remember_missing_user, write_slot
from shopbot.framework.db import transactional
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import Currency, OrderStatus, PaymentProvider, StockDeductStrategy
from shopbot.framework.exceptions.biz import BizException
from shopbot.framework.executor import run_blocking
from shopbot.framework.utils.snowflake import SnowflakeError, next_id
from shopbot.shop.card.service import require_available_cards
from shopbot.shop.delivery.service import deliver_order, prepare_delivery
from shopbot.shop.models import Order, OrderDetail, PaymentNotice, Product, ProductSpec, ShopSetting, User
from shopbot.system.vault import protect, reveal
from shopbot.shop.order.schemas import (
    OrderCreate,
    UserOrderGet,
    UserOrderQuery,
    WalletPayCreate,
    parse_model,
    parse_order_id,
)
from shopbot.shop.order.strategy import stock_strategy
from shopbot.shop.product.service import get_product
from shopbot.shop.user.service import get_user
from shopbot.system.models import AlipayChannel, BepusdtChannel, EpayChannel, WechatChannel
from shopbot.system.payment.confirm import ChannelKeys, VerifiedPayment, confirm_callback
from shopbot.system.payment.drivers import close_remote_payment, copy_payment_party, open_payment, payment_timeout_seconds
from shopbot.system.payment.gateway import ChannelPayment
from shopbot.shop.wallet.service import close_expired_recharges, debit_balance, settle_recharge, to_usdt_cents
from shopbot.system.audit.service import audit
from shopbot.system.payment.service import get_payment_channel

logger = logging.getLogger(__name__)
_DEFAULT_TIMEOUT_SECONDS = 900
_DEFAULT_PENDING_LIMIT = 3
_EXPIRE_BATCH = 100
_ACK = {
    PaymentProvider.EPAY: "success",
    PaymentProvider.ALIPAY: "success",
    PaymentProvider.WECHAT: '{"code":"SUCCESS","message":"成功"}',
    PaymentProvider.BEPUSDT: "ok",
}


@dataclass(frozen=True)
class _ChannelRequest:
    order_id: int
    order_no: str
    subject: str
    amount_cents: int
    currency: int
    network: int | None
    timeout_seconds: int
    cny_rates: dict[int, int]
    channel: object
    detail: object


@audit("order.place")
async def place_order(
    factory: async_sessionmaker[AsyncSession],
    payload: OrderCreate | dict,
) -> tuple[Order, OrderDetail]:
    """先提交待支付订单，再在线程池向渠道要收银台。渠道失败则关单并退回库存。收银台没记下时关闭远程订单。"""
    data = parse_model(OrderCreate, payload)
    prepare_user_lookup(data.user_id)
    async with write_slot():
        pending = await _create_pending_order(factory, data)
    payment = None
    try:
        payment = await run_blocking(
            open_payment,
            pending.channel,
            pending.detail,
            order_no=pending.order_no,
            subject=pending.subject,
            amount_cents=pending.amount_cents,
            currency=pending.currency,
            network=pending.network,
            timeout_seconds=pending.timeout_seconds,
            cny_rates=pending.cny_rates,
        )
        return await _store_channel_payment(factory, pending.order_id, payment)
    except Exception:
        try:
            await _abort_pending_order(factory, pending.order_id)
        except Exception:
            logger.exception("关闭本地待支付订单失败 order_id=%s", pending.order_id)
        if payment is not None:
            try:
                await run_blocking(close_remote_payment, pending.channel, pending.detail, payment.channel_trade_no)
            except Exception:
                logger.exception("关闭远程支付订单失败 order_no=%s", pending.order_no)
        raise


async def _create_pending_order(factory: async_sessionmaker[AsyncSession], data: OrderCreate) -> _ChannelRequest:
    async with factory() as session:
        try:
            return await _insert_pending_order(session, data)
        except BizException as exc:
            if exc.message == "用户不存在":
                remember_missing_user(data.user_id)
            raise


@transactional
async def _insert_pending_order(session: AsyncSession, data: OrderCreate) -> _ChannelRequest:
    user = await _active_user(session, data.user_id)
    product, spec = await _sale_target(session, data.product_id, data.spec_id)
    channel, detail = await get_payment_channel(session, data.channel_id)
    if not channel.is_active:
        raise BizException("支付渠道未启用", code=BizCode.CONFLICT)
    setting = await _shop_setting(session)
    now = datetime.now(timezone.utc)
    await _reject_when_too_many_pending(session, user.id, setting.max_pending_orders, now)
    strategy = stock_strategy(product.stock_deduct_strategy)
    quantity = strategy.quantity(data.quantity)
    if product.stock_deduct_strategy == StockDeductStrategy.DAILY:
        await require_available_cards(session, product, spec, quantity)
    unit_cents = product.price_cents if spec is None else spec.price_cents
    network = data.network if channel.provider == PaymentProvider.BEPUSDT else None
    timeout_seconds = payment_timeout_seconds(channel.provider, setting.order_timeout_seconds)
    try:
        order_id = next_id()
    except SnowflakeError as exc:
        raise BizException("订单号生成失败", code=BizCode.CONFLICT) from exc
    order = Order(
        id=order_id,
        order_no=str(order_id),
        user_id=user.id,
        product_id=product.id,
        spec_id=None if spec is None else spec.id,
        channel_id=channel.id,
        product_name=product.name,
        product_number=quantity,
        stock_deduct_strategy=product.stock_deduct_strategy,
        pay_provider=channel.provider,
        pay_network=network,
        currency=product.currency,
        amount_cents=unit_cents * quantity,
        status=OrderStatus.PENDING,
        expires_at=now + timedelta(seconds=timeout_seconds),
    )
    session.add(order)
    await session.flush()
    await strategy.after_create(session, order)
    copied_channel, copied_detail = copy_payment_party(channel, detail)
    return _ChannelRequest(
        order_id=order.id,
        order_no=order.order_no,
        subject=product.name,
        amount_cents=order.amount_cents,
        currency=order.currency,
        network=network,
        timeout_seconds=timeout_seconds,
        cny_rates=dict(_cny_rates(setting)),
        channel=copied_channel,
        detail=copied_detail,
    )


async def _store_channel_payment(
    factory: async_sessionmaker[AsyncSession],
    order_id: int,
    payment: ChannelPayment,
) -> tuple[Order, OrderDetail]:
    async with factory() as session:
        async with session.begin():
            order = await session.get(Order, order_id)
            if order is None or order.status != OrderStatus.PENDING:
                raise BizException("订单不在待支付", code=BizCode.CONFLICT)
            detail = OrderDetail(
                order_id=order.id,
                channel_trade_no=payment.channel_trade_no or None,
                pay_amount=payment.pay_amount,
                pay_target=protect(payment.pay_target),
                pay_url=protect(payment.pay_url),
            )
            session.add(detail)
            await session.flush()
            session.expunge(order)
            session.expunge(detail)
        detail.pay_target = reveal(detail.pay_target)
        detail.pay_url = reveal(detail.pay_url)
        return order, detail


async def _abort_pending_order(factory: async_sessionmaker[AsyncSession], order_id: int) -> None:
    async with factory() as session:
        async with session.begin():
            order = await session.get(Order, order_id)
            if order is None or order.status != OrderStatus.PENDING:
                return
            await _close_pending(session, order, OrderStatus.CANCELLED)


@audit("order.pay_wallet")
async def pay_by_wallet(
    factory: async_sessionmaker[AsyncSession],
    payload: WalletPayCreate | dict,
    *,
    sender=None,
) -> int:
    """用钱包余额购买。扣款、改已支付和占用卡密在同一事务。自动发货在提交后发送。"""
    data = parse_model(WalletPayCreate, payload)
    prepare_user_lookup(data.user_id)
    try:
        async with factory() as session:
            order_id, auto = await _pay_with_wallet(session, data)
    except BizException as exc:
        if exc.message == "用户不存在":
            remember_missing_user(data.user_id)
        raise
    if auto:
        await deliver_order(factory, order_id, sender=sender, quiet=True)
    return order_id


@transactional
async def _pay_with_wallet(session: AsyncSession, payload: WalletPayCreate | dict) -> tuple[int, bool]:
    data = parse_model(WalletPayCreate, payload)
    user = await _active_user(session, data.user_id)
    product, spec = await _sale_target(session, data.product_id, data.spec_id)
    setting = await _shop_setting(session)
    strategy = stock_strategy(product.stock_deduct_strategy)
    quantity = strategy.quantity(data.quantity)
    await require_available_cards(session, product, spec, quantity)
    unit_cents = product.price_cents if spec is None else spec.price_cents
    charge_cents = unit_cents * quantity
    debit_cents = to_usdt_cents(charge_cents, product.currency, _cny_rates(setting))
    try:
        order_id = next_id()
    except SnowflakeError as exc:
        raise BizException("订单号生成失败", code=BizCode.CONFLICT) from exc
    now = datetime.now(timezone.utc)
    order = Order(
        id=order_id,
        order_no=str(order_id),
        user_id=user.id,
        product_id=product.id,
        spec_id=None if spec is None else spec.id,
        channel_id=None,
        product_name=product.name,
        product_number=quantity,
        stock_deduct_strategy=product.stock_deduct_strategy,
        pay_provider=PaymentProvider.WALLET,
        pay_network=None,
        currency=product.currency,
        amount_cents=charge_cents,
        status=OrderStatus.PAID,
        paid_at=now,
    )
    session.add(order)
    await session.flush()
    await debit_balance(
        session,
        {
            "user_id": user.id,
            "amount_usdt_cents": debit_cents,
            "reason": "购买商品",
            "ref_type": "order",
            "ref_id": order.order_no,
        },
    )
    session.add(
        OrderDetail(
            order_id=order.id,
            pay_amount=_yuan_text(debit_cents),
        )
    )
    await session.flush()
    await strategy.after_create(session, order)
    await stock_strategy(order.stock_deduct_strategy).after_paid(session, order)
    auto = await prepare_delivery(session, order, product)
    return order.id, auto


async def accept_payment_callback(
    factory: async_sessionmaker[AsyncSession],
    channel_id: int,
    *,
    form: dict[str, str] | None = None,
    data: dict | None = None,
    opener=None,
    sender=None,
) -> str:
    """验签并向渠道查单。查单结果与本地订单一致后，自动发货商品才发给买家。"""
    async with factory() as session:
        channel, detail = await get_payment_channel(session, channel_id)
        keys = _channel_keys(channel.provider, channel.epay_method, detail)
        provider = channel.provider
    arguments: dict = {"form": form, "data": data}
    if opener is not None:
        arguments["opener"] = opener
    verified = await asyncio.to_thread(lambda: confirm_callback(keys, **arguments))
    async with factory() as session:
        async with session.begin():
            auto_order_id = await _settle_verified_payment(
                session, channel_id, provider, verified, _payload(form, data)
            )
    if auto_order_id is not None:
        await deliver_order(factory, auto_order_id, sender=sender, quiet=True)
    return _ACK.get(PaymentProvider(provider), "success")


async def _settle_verified_payment(
    session: AsyncSession,
    channel_id: int,
    provider: int,
    verified: VerifiedPayment,
    payload: str,
) -> int | None:
    order = await session.scalar(select(Order).where(Order.order_no == verified.order_no))
    if order is None:
        await settle_recharge(session, channel_id, provider, verified, payload)
        return None
    if order.channel_id != channel_id or order.pay_provider != provider:
        raise BizException("支付渠道不符", code=BizCode.CONFLICT)
    detail = await session.get(OrderDetail, order.id)
    if detail is None or not _same_money(verified.paid_amount, detail.pay_amount):
        raise BizException("支付金额不符", code=BizCode.CONFLICT)
    if verified.fiat_amount is not None and not _same_money(verified.fiat_amount, _yuan_text(order.amount_cents)):
        raise BizException("支付金额不符", code=BizCode.CONFLICT)
    await _save_notice(session, verified, payload)
    if order.status in (OrderStatus.PAID, OrderStatus.DELIVERED):
        return None
    if order.status not in (OrderStatus.PENDING, OrderStatus.EXPIRED, OrderStatus.CANCELLED):
        raise BizException("订单不在待支付", code=BizCode.CONFLICT)
    result = await session.execute(
        update(Order)
        .where(Order.id == order.id, Order.status == order.status)
        .values(status=OrderStatus.PAID, paid_at=datetime.now(timezone.utc))
    )
    if result.rowcount != 1:
        raise BizException("订单状态已变化", code=BizCode.CONFLICT)
    product = await session.get(Product, order.product_id)
    if product is None:
        raise BizException("商品不存在", code=BizCode.NOT_FOUND)
    strategy = stock_strategy(order.stock_deduct_strategy)
    if order.status != OrderStatus.PENDING:
        await strategy.after_create(session, order)
    await strategy.after_paid(session, order)
    if await prepare_delivery(session, order, product):
        return order.id
    return None


async def _save_notice(session: AsyncSession, verified: VerifiedPayment, payload: str) -> None:
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


def _channel_keys(provider: int, epay_method: int | None, detail: object) -> ChannelKeys:
    if isinstance(detail, EpayChannel):
        return ChannelKeys(
            provider=provider,
            epay_method=epay_method,
            merchant_id=detail.merchant_id,
            merchant_key=detail.merchant_key,
            api_url=detail.api_url,
        )
    if isinstance(detail, AlipayChannel):
        return ChannelKeys(
            provider=provider,
            app_id=detail.app_id,
            private_key=detail.private_key,
            alipay_public_key=detail.alipay_public_key,
            api_url=detail.api_url,
        )
    if isinstance(detail, WechatChannel):
        return ChannelKeys(
            provider=provider,
            app_id=detail.app_id,
            merchant_id=detail.merchant_id,
            api_v3_key=detail.api_v3_key,
            cert_serial=detail.cert_serial,
            private_key_path=detail.private_key_path,
        )
    if isinstance(detail, BepusdtChannel):
        return ChannelKeys(provider=provider, api_url=detail.api_url, api_token=detail.api_token)
    raise BizException("支付渠道配置不存在", code=BizCode.NOT_FOUND)


def _payload(form: dict[str, str] | None, data: dict | None) -> str:
    return json.dumps(form if form is not None else data, ensure_ascii=False, default=str)


def _same_money(left: object, right: object) -> bool:
    try:
        return Decimal(str(left)).normalize() == Decimal(str(right)).normalize()
    except Exception:
        return False


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _yuan_text(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


@dataclass(frozen=True)
class OrderPage:
    items: list[Order]
    page: int
    page_size: int
    has_more: bool


@dataclass(frozen=True)
class OwnedOrder:
    """买家本人的订单。卡密只在已支付或已发货时带出。"""

    order: Order
    pay_url: str | None
    pay_target: str | None
    pay_amount: str | None
    secret: str | None


async def list_user_orders(session: AsyncSession, payload: UserOrderQuery | dict) -> OrderPage:
    """按用户分页列出订单，不带卡密。"""
    data = parse_model(UserOrderQuery, payload)
    await get_user(session, data.user_id)
    offset = (data.page - 1) * data.page_size
    rows = list(
        await session.scalars(
            select(Order)
            .where(Order.user_id == data.user_id)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .offset(offset)
            .limit(data.page_size + 1)
        )
    )
    has_more = len(rows) > data.page_size
    return OrderPage(items=rows[: data.page_size], page=data.page, page_size=data.page_size, has_more=has_more)


async def get_user_order(session: AsyncSession, payload: UserOrderGet | dict) -> OwnedOrder:
    """读取买家自己的订单。别人的订单号按不存在处理。"""
    data = parse_model(UserOrderGet, payload)
    await get_user(session, data.user_id)
    order = await session.get(Order, data.order_id)
    if order is None or order.user_id != data.user_id:
        raise BizException("订单不存在", code=BizCode.NOT_FOUND)
    detail = await session.get(OrderDetail, order.id)
    secret = None
    if detail is not None and detail.secret and order.status in {
        OrderStatus.PAID,
        OrderStatus.DELIVERED,
        OrderStatus.FULFILL_FAILED,
    }:
        secret = reveal(detail.secret)
    return OwnedOrder(
        order=order,
        pay_url=None if detail is None else reveal(detail.pay_url),
        pay_target=None if detail is None else reveal(detail.pay_target),
        pay_amount=None if detail is None else detail.pay_amount,
        secret=secret,
    )


@audit("order.cancel")
@transactional
async def cancel_user_order(session: AsyncSession, payload: UserOrderGet | dict) -> Order:
    """买家关闭自己的待支付订单。"""
    data = parse_model(UserOrderGet, payload)
    order = await _pending_order(session, data.order_id)
    if order.user_id != data.user_id:
        raise BizException("订单不存在", code=BizCode.NOT_FOUND)
    await _close_pending(session, order, OrderStatus.CANCELLED)
    return order


@transactional
async def close_order(session: AsyncSession, order_id: int) -> Order:
    order = await _pending_order(session, order_id)
    await _close_pending(session, order, OrderStatus.CANCELLED)
    return order


async def close_expired_orders(factory: async_sessionmaker[AsyncSession]) -> int:
    """关闭已过期且仍未支付的订单。每批最多 100 笔，走 ix_orders_status_expires。"""
    now = datetime.now(timezone.utc)
    async with factory() as session:
        order_ids = list(
            await session.scalars(
                select(Order.id)
                .where(
                    Order.status == OrderStatus.PENDING,
                    Order.expires_at.is_not(None),
                    Order.expires_at <= now,
                )
                .order_by(Order.expires_at)
                .limit(_EXPIRE_BATCH)
            )
        )
    closed = 0
    for order_id in order_ids:
        async with factory() as session:
            async with session.begin():
                if await _expire_one(session, order_id, datetime.now(timezone.utc)):
                    closed += 1
    closed += await close_expired_recharges(factory)
    return closed


async def _expire_one(session: AsyncSession, order_id: int, now: datetime) -> bool:
    order = await session.get(Order, order_id)
    if order is None or order.status != OrderStatus.PENDING:
        return False
    if order.expires_at is None or _as_utc(order.expires_at) > now:
        return False
    await _close_pending(session, order, OrderStatus.EXPIRED)
    return True


async def _close_pending(session: AsyncSession, order: Order, status: str) -> None:
    product = await session.get(Product, order.product_id)
    if product is None:
        raise BizException("商品不存在", code=BizCode.NOT_FOUND)
    await stock_strategy(order.stock_deduct_strategy).after_close(session, order)
    order.status = status
    await session.flush()


async def _active_user(session: AsyncSession, user_id: int) -> User:
    user = await get_user(session, user_id)
    if not user.is_active:
        raise BizException("用户不可用", code=BizCode.CONFLICT)
    return user


async def _sale_target(
    session: AsyncSession,
    product_id: int,
    spec_id: int | None,
) -> tuple[Product, ProductSpec | None]:
    product, _detail = await get_product(session, product_id)
    if not product.is_active:
        raise BizException("商品已下架", code=BizCode.CONFLICT)
    has_specs = await session.scalar(
        select(ProductSpec.id).where(ProductSpec.product_id == product.id).limit(1)
    )
    if has_specs is not None and spec_id is None:
        raise BizException("该商品有规格，请选择规格", code=BizCode.BAD_REQUEST)
    if has_specs is None and spec_id is not None:
        raise BizException("该商品没有规格", code=BizCode.BAD_REQUEST)
    if spec_id is None:
        return product, None
    spec = await session.get(ProductSpec, spec_id)
    if spec is None or spec.product_id != product.id:
        raise BizException("规格不存在", code=BizCode.NOT_FOUND)
    if not spec.is_active:
        raise BizException("规格已下架", code=BizCode.CONFLICT)
    return product, spec


def _cny_rates(setting: ShopSetting) -> dict[int, int]:
    return {
        Currency.USD: setting.usd_cny_cents,
        Currency.USDT: setting.usdt_cny_cents,
        Currency.GBP: setting.gbp_cny_cents,
        Currency.EUR: setting.eur_cny_cents,
    }


async def _shop_setting(session: AsyncSession) -> ShopSetting:
    setting = await session.get(ShopSetting, 1)
    if setting is not None:
        return setting
    setting = ShopSetting(
        id=1,
        order_timeout_seconds=_DEFAULT_TIMEOUT_SECONDS,
        max_pending_orders=_DEFAULT_PENDING_LIMIT,
    )
    session.add(setting)
    await session.flush()
    return setting


async def _reject_when_too_many_pending(
    session: AsyncSession,
    user_id: int,
    limit: int,
    now: datetime,
) -> None:
    online_count = await session.scalar(
        select(func.count())
        .select_from(Order)
        .where(
            Order.user_id == user_id,
            Order.status == OrderStatus.PENDING,
            Order.expires_at > now,
        )
    )
    if online_count is not None and online_count >= limit:
        raise BizException("请关闭订单", code=BizCode.CONFLICT)


async def _pending_order(session: AsyncSession, order_id: int) -> Order:
    order = await session.get(Order, parse_order_id(order_id))
    if order is None:
        raise BizException("订单不存在", code=BizCode.NOT_FOUND)
    if order.status != OrderStatus.PENDING:
        raise BizException("订单不在待支付", code=BizCode.CONFLICT)
    return order
