"""买家的订单列表、订单详情和取消待支付订单。"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from shopbot.bot.formatting import (
    blocks,
    bold,
    button_label,
    clip,
    esc,
    fact_link,
    facts,
    format_cents,
    format_time,
    order_status_label,
    secret_block,
    shelf,
)
from shopbot.bot.i18n import say
from shopbot.bot.identity import open_buyer
from shopbot.bot.ui import buyer_menu, callback_int, guarded, pager, session_factory, show
from shopbot.framework.enums.model import OrderStatus
from shopbot.shop.order.service import cancel_user_order, get_user_order, list_user_orders


@guarded("bot:buyer")
async def orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = "" if query is None else query.data or ""
    if data.startswith("od:"):
        await _detail(update, context, callback_int(data, 1))
        return
    if data.startswith("ox:"):
        await _cancel(update, context, callback_int(data, 1))
        return
    page = 1 if not data.startswith("o:") else callback_int(data, 1)
    await _list(update, context, page)


async def _list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        buyer = await open_buyer(session, user.id, user.username)
        result = await list_user_orders(session, {"user_id": buyer.id, "page": page, "page_size": 5})
        rows: list[list[InlineKeyboardButton]] = []
        cards: list[str] = []
        if not result.items:
            text = blocks(bold(say("我的订单", "My orders")), esc(say("还没有订单。", "No orders yet.")))
        else:
            for order in result.items:
                cards.append(
                    shelf(
                        say("{name} × {count}", "{name} × {count}", name=order.product_name, count=order.product_number),
                        say(
                            "{order_no} · {amount} · {status}",
                            "{order_no} · {amount} · {status}",
                            order_no=order.order_no,
                            amount=format_cents(order.amount_cents, order.currency),
                            status=order_status_label(order.status),
                        ),
                    )
                )
                rows.append(
                    [
                        InlineKeyboardButton(
                            button_label(f"{order.product_name} · {order_status_label(order.status)}"),
                            callback_data=f"od:{order.id}",
                        )
                    ]
                )
            text = blocks(bold(say("我的订单", "My orders")), *cards)
        extra = pager("o", result.page, result.has_more)
        if extra:
            rows.append(extra)
        rows.append([InlineKeyboardButton(say("返回主菜单", "Main menu"), callback_data="m:home")])
    await show(update, text, InlineKeyboardMarkup(rows))


async def _detail(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        buyer = await open_buyer(session, user.id, user.username)
        owned = await get_user_order(session, {"user_id": buyer.id, "order_id": order_id})
        order = owned.order
        parts = [
            bold(order.product_name),
            facts(
                (say("订单号", "Order no."), order.order_no),
                (say("数量", "Quantity"), str(order.product_number)),
                (say("金额", "Amount"), format_cents(order.amount_cents, order.currency)),
                (say("状态", "Status"), order_status_label(order.status)),
                (say("创建", "Created"), format_time(order.created_at)),
            ),
        ]
        if order.status == OrderStatus.PENDING:
            parts.append(esc(say("请在 {when} 前支付。", "Pay before {when}.", when=format_time(order.expires_at))))
            if owned.pay_amount:
                parts.append(facts((say("实付", "Pay"), owned.pay_amount)))
            if owned.pay_target:
                parts.append(facts((say("收款", "Pay to"), owned.pay_target)))
            if owned.pay_url:
                parts.append(fact_link(say("支付链接", "Payment link"), owned.pay_url))
        if owned.secret:
            parts.append(blocks(bold(say("卡密", "Card")), secret_block(clip(owned.secret, 3000))))
        rows = []
        if order.status == OrderStatus.PENDING:
            rows.append([InlineKeyboardButton(say("取消订单", "Cancel order"), callback_data=f"ox:{order.id}")])
        rows.append([InlineKeyboardButton(say("返回订单", "Orders"), callback_data="o:1")])
    await show(update, blocks(*parts), InlineKeyboardMarkup(rows))


async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        buyer = await open_buyer(session, user.id, user.username)
        order = await cancel_user_order(session, {"user_id": buyer.id, "order_id": order_id})
        order_no = order.order_no
    await show(update, esc(say("已取消订单 {order_no}。", "Cancelled order {order_no}.", order_no=order_no)), buyer_menu())
