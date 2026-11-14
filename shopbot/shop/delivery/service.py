"""支付成功后发货。

自动发货的商品立刻把订单号、商品名称、商品详情、卡密和感谢话语发给买家。
手动发货只留下待发送记录，由管理员再调用发货。
消息正文不入库。deliveries 只记是否送达、尝试次数和失败原因。
"""

import asyncio
import html
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shopbot.framework.client.telegram import send_telegram_message
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import CardStatus, DeliveryMethod, DeliveryStatus, OrderStatus
from shopbot.shop.delivery.schemas import DeliveryPageQuery, parse_model
from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.models import Card, Delivery, Order, OrderDetail, Product, ProductDetail, ShopSetting, User
from shopbot.system.vault import protect, reveal

Sender = Callable[[int, str], None]
_MESSAGE_MAX = 4096


async def prepare_delivery(session: AsyncSession, order: Order, product: Product) -> bool:
    """把已售卡密写入订单详情，并记下待发送。自动发货返回 True。"""
    secrets = list(
        await session.scalars(
            select(Card.secret)
            .where(Card.order_id == order.id, Card.status == CardStatus.SOLD)
            .order_by(Card.id)
        )
    )
    if len(secrets) != order.product_number:
        raise BizException("没有可发货的卡密", code=BizCode.CONFLICT)
    detail = await session.get(OrderDetail, order.id)
    if detail is None:
        raise BizException("订单不存在", code=BizCode.NOT_FOUND)
    detail.secret = protect("\n".join(reveal(secret) or "" for secret in secrets))
    existing = await session.scalar(select(Delivery.id).where(Delivery.order_id == order.id))
    if existing is None:
        session.add(Delivery(order_id=order.id, status=DeliveryStatus.PENDING))
    await session.flush()
    return product.delivery_method == DeliveryMethod.AUTO


async def deliver_order(
    factory: async_sessionmaker[AsyncSession],
    order_id: int,
    *,
    sender: Sender | None = None,
    quiet: bool = False,
) -> None:
    """把待发送的订单发给买家。自动发货失败时不把异常抛回支付回调。"""
    sender = sender or send_telegram_message
    try:
        chat_id, text = await _message(factory, order_id)
        if len(text) > _MESSAGE_MAX:
            raise BizException("消息过长", code=BizCode.BAD_REQUEST)
        await asyncio.to_thread(sender, chat_id, text)
    except Exception as exc:
        await _record_failure(factory, order_id, exc)
        if quiet:
            return
        if isinstance(exc, BizException):
            raise
        raise BizException("发货失败", code=BizCode.BAD_REQUEST) from exc
    await _record_sent(factory, order_id)


async def _message(factory: async_sessionmaker[AsyncSession], order_id: int) -> tuple[int, str]:
    async with factory() as session:
        order = await session.get(Order, order_id)
        if order is None:
            raise BizException("订单不存在", code=BizCode.NOT_FOUND)
        if order.status == OrderStatus.DELIVERED:
            raise BizException("订单已发货", code=BizCode.CONFLICT)
        if order.status != OrderStatus.PAID:
            raise BizException("订单不在待发货", code=BizCode.CONFLICT)
        delivery = await session.scalar(select(Delivery).where(Delivery.order_id == order.id))
        if delivery is None or delivery.status == DeliveryStatus.SENT:
            raise BizException("订单已发货", code=BizCode.CONFLICT)
        detail = await session.get(OrderDetail, order.id)
        user = await session.get(User, order.user_id)
        description = await session.scalar(
            select(ProductDetail.description).where(ProductDetail.product_id == order.product_id)
        )
        thank_you = await session.scalar(select(ShopSetting.thank_you_message).where(ShopSetting.id == 1))
        if user is None:
            raise BizException("用户不存在", code=BizCode.NOT_FOUND)
        if detail is None or not detail.secret:
            raise BizException("没有可发货的卡密", code=BizCode.CONFLICT)
        return user.telegram_id, _compose(
            language=user.language if user.language in {"zh", "en"} else "zh",
            thank_you=thank_you,
            order_no=order.order_no,
            description=description or "",
            product_name=order.product_name,
            secret=reveal(detail.secret) or "",
        )


def _compose(
    *,
    language: str,
    thank_you: str | None,
    order_no: str,
    description: str,
    product_name: str,
    secret: str,
) -> str:
    english = language == "en"

    def row(label: str, value: str) -> str:
        return f"<b>{html.escape(label)}</b>  {html.escape(value)}"

    lines: list[str] = [f"<b>{html.escape(product_name)}</b>"]
    if thank_you and thank_you.strip():
        lines.append(html.escape(thank_you.strip()))
    lines.append(row("Order no." if english else "订单号", order_no))
    if description.strip():
        lines.append(html.escape(description.strip()))
    lines.append("<b>" + ("Card" if english else "卡密") + "</b>\n<pre>" + html.escape(secret) + "</pre>")
    return "\n\n".join(lines)


@dataclass(frozen=True)
class DeliveryPage:
    items: list[Order]
    page: int
    page_size: int
    has_more: bool


async def list_manual_deliveries(session: AsyncSession, payload: DeliveryPageQuery | dict) -> DeliveryPage:
    """已支付、尚未送达、且商品为手动发货的订单。"""
    data = parse_model(DeliveryPageQuery, payload)
    offset = (data.page - 1) * data.page_size
    rows = list(
        await session.scalars(
            select(Order)
            .join(Delivery, Delivery.order_id == Order.id)
            .join(Product, Product.id == Order.product_id)
            .where(
                Order.status == OrderStatus.PAID,
                Delivery.status == DeliveryStatus.PENDING,
                Product.delivery_method == DeliveryMethod.MANUAL,
            )
            .order_by(Order.id.desc())
            .offset(offset)
            .limit(data.page_size + 1)
        )
    )
    has_more = len(rows) > data.page_size
    return DeliveryPage(items=rows[: data.page_size], page=data.page, page_size=data.page_size, has_more=has_more)


async def _record_sent(factory: async_sessionmaker[AsyncSession], order_id: int) -> None:
    now = datetime.now(timezone.utc)
    async with factory() as session:
        async with session.begin():
            result = await session.execute(
                update(Delivery)
                .where(Delivery.order_id == order_id, Delivery.status == DeliveryStatus.PENDING)
                .values(status=DeliveryStatus.SENT, sent_at=now, attempts=Delivery.attempts + 1, last_error=None)
            )
            if result.rowcount != 1:
                return
            await session.execute(
                update(Order).where(Order.id == order_id, Order.status == OrderStatus.PAID).values(status=OrderStatus.DELIVERED)
            )


async def _record_failure(factory: async_sessionmaker[AsyncSession], order_id: int, exc: Exception) -> None:
    reason = exc.message if isinstance(exc, BizException) else "发货失败"
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(Delivery)
                .where(Delivery.order_id == order_id, Delivery.status == DeliveryStatus.PENDING)
                .values(attempts=Delivery.attempts + 1, last_error=reason[:255])
            )
