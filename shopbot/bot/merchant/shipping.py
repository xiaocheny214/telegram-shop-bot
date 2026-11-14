"""手动发货。列出已支付且尚未送达的订单，确认后发送。"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from shopbot.bot.formatting import blocks, bold, button_label, esc, format_cents, shelf
from shopbot.bot.i18n import say
from shopbot.bot.ui import callback_int, guarded, merchant_menu, merchant_session, pager, session_factory, show
from shopbot.shop.delivery.service import deliver_order, list_manual_deliveries


@guarded("bot:merchant")
async def shipping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = "" if query is None else query.data or ""
    if data.startswith("sq:"):
        await _ask(update, context, callback_int(data, 1))
        return
    if data.startswith("sy:"):
        await _send(update, context, callback_int(data, 1))
        return
    page = 1 if not data.startswith("sg:") else callback_int(data, 1)
    await show_deliveries(update, context, page)


async def show_deliveries(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    async with merchant_session(update, context) as session:
        result = await list_manual_deliveries(session, {"page": page, "page_size": 5})
        buttons: list[list[InlineKeyboardButton]] = []
        cards: list[str] = []
        if not result.items:
            text = blocks(bold(say("待发货", "To deliver")), esc(say("没有待发货订单。", "Nothing is waiting for delivery.")))
        else:
            for order in result.items:
                cards.append(
                    shelf(
                        say("{name} × {count}", "{name} × {count}", name=order.product_name, count=order.product_number),
                        say(
                            "{order_no} · {amount}",
                            "{order_no} · {amount}",
                            order_no=order.order_no,
                            amount=format_cents(order.amount_cents, order.currency),
                        ),
                    )
                )
                buttons.append(
                    [
                        InlineKeyboardButton(
                            button_label(say("发货 {name}", "Deliver {name}", name=order.product_name)),
                            callback_data=f"sq:{order.id}",
                        )
                    ]
                )
            text = blocks(bold(say("待发货", "To deliver")), *cards)
        extra = pager("sg", result.page, result.has_more)
        if extra:
            buttons.append(extra)
        buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data="mm:home")])
    await show(update, text, InlineKeyboardMarkup(buttons))


async def _ask(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    await _actor(update, context)
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(say("确认发货", "Deliver"), callback_data=f"sy:{order_id}")],
            [InlineKeyboardButton(say("返回", "Back"), callback_data="sg:1")],
        ]
    )
    await show(update, blocks(bold(say("确认发货", "Deliver")), esc(say("卡密会发给买家。", "The card will be sent to the buyer."))), markup)


async def _send(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    await _actor(update, context)
    await deliver_order(session_factory(context), order_id)
    await show(update, say("已发给买家。", "Sent to the buyer."), merchant_menu())


async def _actor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with merchant_session(update, context):
        return
