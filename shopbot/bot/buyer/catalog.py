"""浏览分类和商品，然后选择数量与支付方式。"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from shopbot.bot.buyer.menu import start
from shopbot.bot.formatting import (
    blocks,
    bold,
    button_label,
    clip,
    delivery_label,
    esc,
    fact_link,
    facts,
    format_cents,
    format_time,
    marked,
    network_label,
    page_slice,
    parse_quantity,
    provider_label,
    shelf,
)
from shopbot.bot.i18n import say, tr
from shopbot.bot.identity import open_buyer
from shopbot.bot.ui import buyer_id, buyer_menu, callback_int, guarded, pager, pair, remind, rows_of, session_factory, show, show_cover
from shopbot.framework.enums.model import PaymentProvider, UsdtNetwork
from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.catalog.service import get_category, list_categories
from shopbot.shop.order.service import place_order, pay_by_wallet
from shopbot.shop.product.service import get_product, list_products, list_specs
from shopbot.system.payment.service import list_payment_channels

ASK_QTY, ASK_CHANNEL, ASK_NETWORK = range(3)
_PAGE = 5


@guarded("bot:buyer")
async def browse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = "" if query is None else query.data or ""
    if data == "m:home":
        await start(update, context)
        return
    if data in {"m:shop", ""} or data.startswith("c:"):
        page = 1 if not data.startswith("c:") else callback_int(data, 1)
        await _categories(update, context, page)
        return
    if data.startswith("g:"):
        await _products(update, context, callback_int(data, 1), callback_int(data, 2))
        return
    if data.startswith("d:"):
        await _detail(update, context, callback_int(data, 1))


async def _categories(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        await open_buyer(session, user.id, user.username)
        categories = await list_categories(session, active_only=True)
        chunk, has_more = page_slice(categories, page, _PAGE)
        buttons: list[InlineKeyboardButton] = []
        if not categories:
            text = blocks(bold(say("选购", "Shop")), esc(say("现在没有在售分类。", "Nothing is on sale.")))
        else:
            text = blocks(bold(say("选购", "Shop")), esc(say("请选择分类。", "Choose a category.")))
        for category in chunk:
            buttons.append(
                InlineKeyboardButton(button_label(marked(category.icon, category.name)), callback_data=f"g:{category.id}:1")
            )
        rows = rows_of(buttons, 3)
        extra = pager("c", page, has_more)
        if extra:
            rows.append(extra)
        rows.append([InlineKeyboardButton(say("返回主菜单", "Main menu"), callback_data="m:home")])
    await show(update, text, InlineKeyboardMarkup(rows))


async def _products(update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: int, page: int) -> None:
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        await open_buyer(session, user.id, user.username)
        products = await list_products(session, category_id=category_id, active_only=True)
        category = await get_category(session, category_id)
        icon = category.icon
        chunk, has_more = page_slice(products, page, _PAGE)
        rows: list[list[InlineKeyboardButton]] = []
        cards: list[str] = []
        if not products:
            text = blocks(
                bold(marked(icon, category.name)),
                esc(say("这个分类里没有在售商品。", "This category has no products on sale.")),
            )
        else:
            for product in chunk:
                title = marked(icon, product.name)
                cards.append(
                    shelf(
                        title,
                        say(
                            "{price} · 库存 {stock}",
                            "{price} · stock {stock}",
                            price=format_cents(product.price_cents, product.currency),
                            stock=product.stock,
                        ),
                    )
                )
                rows.append([InlineKeyboardButton(button_label(title), callback_data=f"d:{product.id}")])
            text = blocks(
                bold(marked(icon, category.name)),
                esc(say("请选择商品。", "Choose a product.")),
                *cards,
            )
        extra = pager(f"g:{category_id}", page, has_more)
        if extra:
            rows.append(extra)
        rows.append([InlineKeyboardButton(say("返回分类", "Categories"), callback_data="c:1")])
    await show(update, text, InlineKeyboardMarkup(rows))


async def _detail(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        await open_buyer(session, user.id, user.username)
        product, detail = await get_product(session, product_id)
        if not product.is_active:
            await show(update, say("商品已下架。", "This product is unavailable."), buyer_menu())
            return
        category = await get_category(session, product.category_id)
        specs = [spec for spec in await list_specs(session, product.id, active_only=True)]
        description = detail.description.strip()
        cover = product.cover_url
        parts = [
            bold(marked(category.icon, product.name)),
            facts(
                (say("价格", "Price"), format_cents(product.price_cents, product.currency)),
                (say("库存", "Stock"), str(product.stock)),
                (say("已售", "Sold"), str(product.sold_count)),
                (say("发货", "Delivery"), delivery_label(product.delivery_method)),
            ),
        ]
        if description:
            parts.append(esc(clip(description, 800)))
        rows = []
        if specs:
            parts.append(esc(say("选择规格。", "Choose an option.")))
            for spec in specs:
                label = say(
                    "{name}  {price}",
                    "{name}  {price}",
                    name=spec.name,
                    price=format_cents(spec.price_cents, product.currency),
                )
                rows.append([InlineKeyboardButton(button_label(label), callback_data=f"s:{product.id}:{spec.id}")])
        else:
            rows.append([InlineKeyboardButton(say("购买", "Buy"), callback_data=f"s:{product.id}:0")])
        rows.append([InlineKeyboardButton(say("返回商品", "Products"), callback_data=f"g:{product.category_id}:1")])
    body = blocks(*parts)
    markup = InlineKeyboardMarkup(rows)
    if cover:
        await show_cover(update, body, cover, markup)
        return
    await show(update, body, markup)


def checkout_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(_begin, pattern=r"^s:\d+:\d+$")],
        states={
            ASK_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, _quantity)],
            ASK_CHANNEL: [CallbackQueryHandler(_channel, pattern=r"^cch:")],
            ASK_NETWORK: [CallbackQueryHandler(_network, pattern=r"^cnet:\d+$")],
        },
        fallbacks=[
            CommandHandler("cancel", _abort),
            CommandHandler("start", _abort),
            CallbackQueryHandler(_abort, pattern=r"^m:"),
            CallbackQueryHandler(remind),
        ],
        allow_reentry=True,
        name="buyer_checkout",
    )


@guarded("bot:buyer")
async def _begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    assert query is not None and query.data is not None
    spec_id = callback_int(query.data, 2)
    context.user_data["checkout"] = {
        "product_id": callback_int(query.data, 1),
        "spec_id": None if spec_id == 0 else spec_id,
    }
    await show(update, say("请输入购买数量，1 到 10000。发送 /cancel 返回主菜单。", "Enter a quantity from 1 to 10000. Send /cancel to return to the main menu."))
    return ASK_QTY


@guarded("bot:buyer")
async def _quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data.get("checkout")
    message = update.effective_message
    if not isinstance(draft, dict) or message is None or message.text is None:
        return await _abort(update, context)
    try:
        draft["quantity"] = parse_quantity(message.text)
    except BizException as exc:
        await show(update, esc(tr(exc.message)))
        return ASK_QTY
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        await open_buyer(session, user.id, user.username)
        product, _detail = await get_product(session, int(draft["product_id"]))
        channels = [channel for channel in await list_payment_channels(session) if channel.is_active]
        unit = product.price_cents
        if draft["spec_id"] is not None:
            specs = await list_specs(session, product.id, active_only=True)
            matched = next((spec for spec in specs if spec.id == draft["spec_id"]), None)
            if matched is None:
                await show(update, say("规格不存在或已下架。", "That option is missing or unavailable."), buyer_menu())
                return ConversationHandler.END
            unit = matched.price_cents
        amount = unit * int(draft["quantity"])
        draft["summary"] = blocks(
            bold(product.name),
            facts(
                (say("数量", "Quantity"), str(draft["quantity"])),
                (say("应付", "Due"), format_cents(amount, product.currency)),
            ),
        )
        rows = pair(
            [
                InlineKeyboardButton(button_label(provider_label(channel.provider)), callback_data=f"cch:{channel.id}")
                for channel in channels
            ]
        )
        rows.append([InlineKeyboardButton(say("余额支付", "Pay with balance"), callback_data="cch:w")])
        rows.append([InlineKeyboardButton(say("返回主菜单", "Main menu"), callback_data="m:home")])
    await show(update, blocks(draft["summary"], esc(say("选择支付方式。", "Choose how to pay."))), InlineKeyboardMarkup(rows))
    return ASK_CHANNEL


@guarded("bot:buyer")
async def _channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    draft = context.user_data.get("checkout")
    assert query is not None and query.data is not None
    if not isinstance(draft, dict):
        return await _abort(update, context)
    if query.data == "cch:w":
        await _pay_wallet(update, context, draft)
        context.user_data.pop("checkout", None)
        return ConversationHandler.END
    channel_id = callback_int(query.data, 1)
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        await open_buyer(session, user.id, user.username)
        channels = await list_payment_channels(session)
        channel = next((item for item in channels if item.id == channel_id and item.is_active), None)
    if channel is None:
        await show(update, say("支付渠道不可用。", "This payment channel is unavailable."), buyer_menu())
        return ConversationHandler.END
    draft["channel_id"] = channel.id
    if channel.provider == int(PaymentProvider.BEPUSDT):
        rows = [
            [
                InlineKeyboardButton(network_label(int(UsdtNetwork.TRC20)), callback_data=f"cnet:{int(UsdtNetwork.TRC20)}"),
                InlineKeyboardButton(network_label(int(UsdtNetwork.ERC20)), callback_data=f"cnet:{int(UsdtNetwork.ERC20)}"),
            ],
            [
                InlineKeyboardButton(network_label(int(UsdtNetwork.SOLANA)), callback_data=f"cnet:{int(UsdtNetwork.SOLANA)}"),
                InlineKeyboardButton(network_label(int(UsdtNetwork.POLYGON)), callback_data=f"cnet:{int(UsdtNetwork.POLYGON)}"),
            ],
        ]
        await show(update, say("请选择 USDT 网络。", "Choose a USDT network."), InlineKeyboardMarkup(rows))
        return ASK_NETWORK
    await _place(update, context, draft, None)
    context.user_data.pop("checkout", None)
    return ConversationHandler.END


@guarded("bot:buyer")
async def _network(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    draft = context.user_data.get("checkout")
    assert query is not None and query.data is not None
    if not isinstance(draft, dict):
        return await _abort(update, context)
    await _place(update, context, draft, callback_int(query.data, 1))
    context.user_data.pop("checkout", None)
    return ConversationHandler.END


async def _pay_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE, draft: dict) -> None:
    user_id = await buyer_id(update, context)
    order_id = await pay_by_wallet(
        session_factory(context),
        {
            "user_id": user_id,
            "product_id": int(draft["product_id"]),
            "spec_id": draft["spec_id"],
            "quantity": int(draft["quantity"]),
        },
    )
    await show(
        update,
        say(
            "已用余额支付。订单编号 {order_id}。\n自动发货的卡密会随后发到这个聊天，也可以在订单里再次查看。手动发货请等商家发送。",
            "Paid with balance. Order {order_id}.\nAutomatic cards arrive in this chat and stay on the order. Manual delivery waits for the merchant.",
            order_id=order_id,
        ),
        buyer_menu(),
    )


async def _place(update: Update, context: ContextTypes.DEFAULT_TYPE, draft: dict, network: int | None) -> None:
    user_id = await buyer_id(update, context)
    order, detail = await place_order(
        session_factory(context),
        {
            "user_id": user_id,
            "product_id": int(draft["product_id"]),
            "channel_id": int(draft["channel_id"]),
            "spec_id": draft["spec_id"],
            "quantity": int(draft["quantity"]),
            "network": network,
        },
    )
    parts = [
        bold(say("请完成支付", "Complete the payment")),
        facts(
            (say("订单号", "Order no."), order.order_no),
            (say("金额", "Amount"), format_cents(order.amount_cents, order.currency)),
        ),
    ]
    if detail.pay_amount:
        parts.append(facts((say("实付", "Pay"), detail.pay_amount)))
    if detail.pay_target:
        parts.append(facts((say("收款", "Pay to"), detail.pay_target)))
    if detail.pay_url:
        parts.append(fact_link(say("支付链接", "Payment link"), detail.pay_url))
    parts.append(esc(say("请在 {when} 前完成支付。", "Pay before {when}.", when=format_time(order.expires_at))))
    parts.append(esc(say("支付成功后，自动发货的卡密会发到这个聊天。", "After payment, automatic cards are sent to this chat.")))
    await show(update, blocks(*parts), buyer_menu())


@guarded("bot:buyer")
async def _abort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("checkout", None)
    await start(update, context)
    return ConversationHandler.END
