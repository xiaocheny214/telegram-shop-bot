"""商品的新增、编辑、规格、上下架和删除。"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from shopbot.bot.formatting import (
    active_label,
    blocks,
    bold,
    button_label,
    currency_label,
    delivery_label,
    esc,
    facts,
    format_cents,
    page_slice,
    parse_cents,
    shelf,
    strategy_label,
)
from shopbot.bot.i18n import say, tr
from shopbot.bot.ui import callback_int, guarded, merchant_menu, merchant_session, pager, pair, remind, show
from shopbot.framework.enums.model import Currency, DeliveryMethod, StockDeductStrategy
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.catalog.service import get_category, list_categories
from shopbot.shop.product.service import (
    create_product,
    create_spec,
    delete_product,
    delete_spec,
    get_product,
    get_spec,
    list_products,
    list_specs,
    update_product,
    update_spec,
)

ASK_NAME, ASK_PRICE, ASK_CURRENCY, ASK_DELIVERY, ASK_STRATEGY, ASK_DESC, ASK_COVER = range(7)
ASK_SPEC_NAME, ASK_SPEC_PRICE = range(7, 9)
ASK_EDIT_DESC = 9


@guarded("bot:merchant")
async def products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = "" if query is None else query.data or ""
    if data.startswith("pv:"):
        await _view(update, context, callback_int(data, 1))
        return
    if data.startswith("pt:"):
        await _toggle(update, context, callback_int(data, 1))
        return
    if data.startswith("px:"):
        await _ask_delete(update, context, callback_int(data, 1))
        return
    if data.startswith("py:"):
        await _delete(update, context, callback_int(data, 1))
        return
    if data.startswith("pn:"):
        await _pick_category(update, context, callback_int(data, 1))
        return
    if data.startswith("pe:"):
        await _edit(update, context, callback_int(data, 1))
        return
    if data.startswith("zc:"):
        await _pick_currency(update, context, callback_int(data, 1))
        return
    if data.startswith("pz:"):
        await _apply(update, context, callback_int(data, 1), {"currency": callback_int(data, 2)})
        return
    if data.startswith("zd:"):
        await _pick_delivery(update, context, callback_int(data, 1))
        return
    if data.startswith("ph:"):
        await _apply(update, context, callback_int(data, 1), {"delivery_method": callback_int(data, 2)})
        return
    if data.startswith("zs:"):
        await _pick_strategy(update, context, callback_int(data, 1))
        return
    if data.startswith("pj:"):
        await _apply(update, context, callback_int(data, 1), {"stock_deduct_strategy": callback_int(data, 2)})
        return
    if data.startswith("pf:"):
        await _pick_edit_category(update, context, callback_int(data, 1), callback_int(data, 2))
        return
    if data.startswith("pw:"):
        await _apply(update, context, callback_int(data, 1), {"category_id": callback_int(data, 2)})
        return
    if data.startswith("so:"):
        await _open_spec(update, context, callback_int(data, 1))
        return
    if data.startswith("st:"):
        await _toggle_spec(update, context, callback_int(data, 1))
        return
    if data.startswith("sx:"):
        await _ask_delete_spec(update, context, callback_int(data, 1))
        return
    if data.startswith("sz:"):
        await _delete_spec(update, context, callback_int(data, 1))
        return
    page = 1 if not data.startswith("pg:") else callback_int(data, 1)
    await show_products(update, context, page)


async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    async with merchant_session(update, context) as session:
        items = await list_products(session)
        chunk, has_more = page_slice(items, page)
        buttons: list[list[InlineKeyboardButton]] = []
        cards: list[str] = []
        if not items:
            text = blocks(bold(say("商品", "Products")), esc(say("还没有商品。", "No products yet.")))
        else:
            for product in chunk:
                cards.append(
                    shelf(
                        product.name,
                        say(
                            "{price} · {state} · 库存 {stock}",
                            "{price} · {state} · stock {stock}",
                            price=format_cents(product.price_cents, product.currency),
                            state=active_label(product.is_active),
                            stock=product.stock,
                        ),
                    )
                )
                buttons.append([InlineKeyboardButton(button_label(product.name), callback_data=f"pv:{product.id}")])
            text = blocks(bold(say("商品", "Products")), *cards)
        extra = pager("pg", page, has_more)
        if extra:
            buttons.append(extra)
        buttons.append([InlineKeyboardButton(say("新增商品", "New product"), callback_data="pn:1")])
        buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data="mm:home")])
    await show(update, text, InlineKeyboardMarkup(buttons))


async def _view(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    async with merchant_session(update, context) as session:
        product, detail = await get_product(session, product_id)
        specs = await list_specs(session, product.id)
        parts = [
            bold(product.name),
            facts(
                (say("价格", "Price"), format_cents(product.price_cents, product.currency)),
                (say("状态", "Status"), active_label(product.is_active)),
                (say("发货", "Delivery"), delivery_label(product.delivery_method)),
                (say("库存策略", "Stock rule"), strategy_label(product.stock_deduct_strategy)),
            ),
            esc(detail.description or say("（空）", "(empty)")),
        ]
        if specs:
            parts.append(bold(say("规格", "Options")))
            for spec in specs:
                parts.append(
                    shelf(
                        spec.name,
                        say(
                            "{price} · {state}",
                            "{price} · {state}",
                            price=format_cents(spec.price_cents, product.currency),
                            state=active_label(spec.is_active),
                        ),
                    )
                )
        buttons = [
            [
                InlineKeyboardButton(say("商品编辑", "Edit product"), callback_data=f"pe:{product.id}"),
                InlineKeyboardButton(say("加规格", "Add option"), callback_data=f"ps:{product.id}"),
            ],
            [
                InlineKeyboardButton(active_label(not product.is_active), callback_data=f"pt:{product.id}"),
                InlineKeyboardButton(say("删除", "Delete"), callback_data=f"px:{product.id}"),
            ],
            [InlineKeyboardButton(say("返回商品", "Products"), callback_data="pg:1")],
        ]
    await show(update, blocks(*parts), InlineKeyboardMarkup(buttons))


async def _edit(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    async with merchant_session(update, context) as session:
        product, detail = await get_product(session, product_id)
        category = await get_category(session, product.category_id)
        specs = await list_specs(session, product.id)
        cover = product.cover_url or say("（空）", "(empty)")
        remark = detail.internal_remark or say("（空）", "(empty)")
        category_name = category.name
        name = product.name
        price = format_cents(product.price_cents, product.currency)
        delivery = delivery_label(product.delivery_method)
        strategy = strategy_label(product.stock_deduct_strategy)
        sort_order = str(product.sort_order)
        description = detail.description or say("（空）", "(empty)")
        spec_rows = [(spec.id, spec.name, format_cents(spec.price_cents, product.currency), spec.is_active) for spec in specs]
    buttons = [
        [
            InlineKeyboardButton(say("名称", "Name"), callback_data=f"p1:{product_id}"),
            InlineKeyboardButton(say("价格", "Price"), callback_data=f"p2:{product_id}"),
        ],
        [
            InlineKeyboardButton(say("货币", "Currency"), callback_data=f"zc:{product_id}"),
            InlineKeyboardButton(say("说明", "Description"), callback_data=f"pd:{product_id}"),
        ],
        [
            InlineKeyboardButton(say("封面", "Cover"), callback_data=f"pu:{product_id}"),
            InlineKeyboardButton(say("发货方式", "Delivery"), callback_data=f"zd:{product_id}"),
        ],
        [
            InlineKeyboardButton(say("库存策略", "Stock rule"), callback_data=f"zs:{product_id}"),
            InlineKeyboardButton(say("分类", "Category"), callback_data=f"pf:{product_id}:1"),
        ],
        [
            InlineKeyboardButton(say("排序", "Sort"), callback_data=f"pq:{product_id}"),
            InlineKeyboardButton(say("内部备注", "Internal note"), callback_data=f"pm:{product_id}"),
        ],
    ]
    for spec_id, spec_name, spec_price, active in spec_rows:
        buttons.append(
            [InlineKeyboardButton(button_label(f"{spec_name} {spec_price} {active_label(active)}"), callback_data=f"so:{spec_id}")]
        )
    buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data=f"pv:{product_id}")])
    await show(
        update,
        blocks(
            bold(say("商品编辑", "Edit product")),
            bold(name),
            facts(
                (say("价格", "Price"), price),
                (say("发货", "Delivery"), delivery),
                (say("库存策略", "Stock rule"), strategy),
                (say("分类", "Category"), category_name),
                (say("排序", "Sort"), sort_order),
                (say("封面", "Cover"), cover),
                (say("内部备注", "Internal note"), remark),
            ),
            esc(description),
        ),
        InlineKeyboardMarkup(buttons),
    )


async def _apply(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, payload: dict) -> None:
    async with merchant_session(update, context) as session:
        await update_product(session, product_id, payload)
    await _edit(update, context, product_id)


async def _pick_currency(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    rows = pair([InlineKeyboardButton(currency_label(int(item)), callback_data=f"pz:{product_id}:{int(item)}") for item in Currency])
    rows.append([InlineKeyboardButton(say("返回", "Back"), callback_data=f"pe:{product_id}")])
    await show(update, say("请选择货币。", "Choose a currency."), InlineKeyboardMarkup(rows))


async def _pick_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    rows = [
        [
            InlineKeyboardButton(say("自动发货", "Automatic"), callback_data=f"ph:{product_id}:{int(DeliveryMethod.AUTO)}"),
            InlineKeyboardButton(say("手动发货", "Manual"), callback_data=f"ph:{product_id}:{int(DeliveryMethod.MANUAL)}"),
        ],
        [InlineKeyboardButton(say("返回", "Back"), callback_data=f"pe:{product_id}")],
    ]
    await show(update, say("请选择发货方式。新订单使用这个方式。", "Choose how it is delivered. New orders use this method."), InlineKeyboardMarkup(rows))


async def _pick_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    rows = [
        [
            InlineKeyboardButton(say("下单占用", "On order"), callback_data=f"pj:{product_id}:{int(StockDeductStrategy.DAILY)}"),
            InlineKeyboardButton(say("支付后占用", "After payment"), callback_data=f"pj:{product_id}:{int(StockDeductStrategy.MARKETING)}"),
        ],
        [InlineKeyboardButton(say("返回", "Back"), callback_data=f"pe:{product_id}")],
    ]
    await show(update, say("请选择库存策略。已经生成的订单仍按原策略处理。", "Choose when stock is reserved. Orders already placed keep their original rule."), InlineKeyboardMarkup(rows))


async def _pick_edit_category(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, page: int) -> None:
    async with merchant_session(update, context) as session:
        categories = await list_categories(session, active_only=False)
        chunk, has_more = page_slice(categories, page)
        buttons = [
            [InlineKeyboardButton(button_label(category.name), callback_data=f"pw:{product_id}:{category.id}")]
            for category in chunk
        ]
        text = blocks(bold(say("商品编辑", "Edit product")), esc(say("选择商品所属分类。", "Choose a category for the product.")))
        extra = pager(f"pf:{product_id}", page, has_more)
        if extra:
            buttons.append(extra)
        buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data=f"pe:{product_id}")])
    await show(update, text, InlineKeyboardMarkup(buttons))


async def _open_spec(update: Update, context: ContextTypes.DEFAULT_TYPE, spec_id: int) -> None:
    async with merchant_session(update, context) as session:
        specs = await _spec_of(session, spec_id)
        spec, product = specs
        price = format_cents(spec.price_cents, product.currency)
        name = spec.name
        active = spec.is_active
        product_id = product.id
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(say("改名", "Rename"), callback_data=f"sn:{spec_id}"),
                InlineKeyboardButton(say("改价", "Price"), callback_data=f"sp:{spec_id}"),
            ],
            [
                InlineKeyboardButton(active_label(not active), callback_data=f"st:{spec_id}"),
                InlineKeyboardButton(say("删除", "Delete"), callback_data=f"sx:{spec_id}"),
            ],
            [InlineKeyboardButton(say("返回", "Back"), callback_data=f"pe:{product_id}")],
        ]
    )
    await show(update, blocks(bold(name), facts((say("价格", "Price"), price), (say("状态", "Status"), active_label(active)))), markup)


async def _toggle_spec(update: Update, context: ContextTypes.DEFAULT_TYPE, spec_id: int) -> None:
    async with merchant_session(update, context) as session:
        spec, _product = await _spec_of(session, spec_id)
        await update_spec(session, spec_id, {"is_active": not spec.is_active})
    await _open_spec(update, context, spec_id)


async def _ask_delete_spec(update: Update, context: ContextTypes.DEFAULT_TYPE, spec_id: int) -> None:
    async with merchant_session(update, context) as session:
        spec, _product = await _spec_of(session, spec_id)
        name = spec.name
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(say("确认删除", "Delete"), callback_data=f"sz:{spec_id}")],
            [InlineKeyboardButton(say("返回", "Back"), callback_data=f"so:{spec_id}")],
        ]
    )
    await show(update, blocks(bold(name), esc(say("已有订单或卡密时会拒绝。", "It is refused when the option has orders or cards."))), markup)


async def _delete_spec(update: Update, context: ContextTypes.DEFAULT_TYPE, spec_id: int) -> None:
    async with merchant_session(update, context) as session:
        spec, product = await _spec_of(session, spec_id)
        product_id = product.id
        await delete_spec(session, spec.id)
    await _edit(update, context, product_id)


async def _spec_of(session, spec_id: int):
    spec = await get_spec(session, spec_id)
    product, _detail = await get_product(session, spec.product_id)
    return spec, product


async def _pick_category(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    async with merchant_session(update, context) as session:
        categories = await list_categories(session, active_only=False)
        chunk, has_more = page_slice(categories, page)
        buttons = [
            [InlineKeyboardButton(button_label(category.name), callback_data=f"pc:{category.id}")] for category in chunk
        ]
        if not categories:
            text = blocks(bold(say("新增商品", "New product")), esc(say("请先新增分类。", "Create a category first.")))
        else:
            text = blocks(bold(say("新增商品", "New product")), esc(say("选择商品所属分类。", "Choose a category for the product.")))
        extra = pager("pn", page, has_more)
        if extra:
            buttons.append(extra)
        buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data="pg:1")])
    await show(update, text, InlineKeyboardMarkup(buttons))


async def _toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    async with merchant_session(update, context) as session:
        product, _detail = await get_product(session, product_id)
        await update_product(session, product_id, {"is_active": not product.is_active})
    await _view(update, context, product_id)


async def _ask_delete(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    await _actor(update, context)
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(say("确认删除", "Delete"), callback_data=f"py:{product_id}")],
            [InlineKeyboardButton(say("返回", "Back"), callback_data=f"pv:{product_id}")],
        ]
    )
    await show(update, blocks(bold(say("删除商品", "Delete product")), esc(say("有未发货的手动订单、待支付的自动发货订单或卡密时会拒绝。", "It is refused when an unshipped manual order, an unpaid automatic order, or cards remain."))), markup)


async def _delete(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    async with merchant_session(update, context) as session:
        await delete_product(session, product_id)
    await show_products(update, context, 1)


def product_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(_begin, pattern=r"^pc:\d+$")],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, _name)],
            ASK_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, _price)],
            ASK_CURRENCY: [CallbackQueryHandler(_currency, pattern=r"^cur:\d+$")],
            ASK_DELIVERY: [CallbackQueryHandler(_delivery, pattern=r"^dlv:\d+$")],
            ASK_STRATEGY: [CallbackQueryHandler(_strategy, pattern=r"^stk:\d+$")],
            ASK_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, _description)],
            ASK_COVER: [MessageHandler(filters.TEXT & ~filters.COMMAND, _cover)],
        },
        fallbacks=_fallbacks(),
        allow_reentry=True,
        name="merchant_product",
    )


def spec_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(_spec_begin, pattern=r"^(ps:\d+|sn:\d+|sp:\d+)$")],
        states={
            ASK_SPEC_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, _spec_name)],
            ASK_SPEC_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, _spec_price)],
        },
        fallbacks=_fallbacks(),
        allow_reentry=True,
        name="merchant_spec",
    )


def product_edit_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(_edit_begin, pattern=r"^(p1:\d+|p2:\d+|pd:\d+|pu:\d+|pq:\d+|pm:\d+)$")],
        states={ASK_EDIT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, _edit_save)]},
        fallbacks=_fallbacks(),
        allow_reentry=True,
        name="merchant_product_edit",
    )


@guarded("bot:merchant")
async def _begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    assert query is not None and query.data is not None
    context.user_data["product_form"] = {"category_id": callback_int(query.data, 1)}
    await show(update, say("请发送商品名称。发送 /cancel 返回。", "Send the product name. Send /cancel to go back."))
    return ASK_NAME


@guarded("bot:merchant")
async def _name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _take_text(update, context, "name", say("请发送价格，单位是元，例如 9.99。", "Send the price in yuan, for example 9.99."), ASK_PRICE)


@guarded("bot:merchant")
async def _price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    form = _form(context)
    message = update.effective_message
    if form is None or message is None or message.text is None:
        return await _abort(update, context)
    try:
        form["price_cents"] = parse_cents(message.text)
    except BizException as exc:
        await show(update, esc(tr(exc.message)))
        return ASK_PRICE
    rows = pair([InlineKeyboardButton(currency_label(int(item)), callback_data=f"cur:{int(item)}") for item in Currency])
    await show(update, say("请选择货币。", "Choose a currency."), InlineKeyboardMarkup(rows))
    return ASK_CURRENCY


@guarded("bot:merchant")
async def _currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    form = _form(context)
    query = update.callback_query
    if form is None or query is None or query.data is None:
        return await _abort(update, context)
    form["currency"] = callback_int(query.data, 1)
    rows = [
        [
            InlineKeyboardButton(say("自动发货", "Automatic"), callback_data=f"dlv:{int(DeliveryMethod.AUTO)}"),
            InlineKeyboardButton(say("手动发货", "Manual"), callback_data=f"dlv:{int(DeliveryMethod.MANUAL)}"),
        ]
    ]
    await show(update, say("请选择发货方式。", "Choose how it is delivered."), InlineKeyboardMarkup(rows))
    return ASK_DELIVERY


@guarded("bot:merchant")
async def _delivery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    form = _form(context)
    query = update.callback_query
    if form is None or query is None or query.data is None:
        return await _abort(update, context)
    form["delivery_method"] = callback_int(query.data, 1)
    rows = [
        [
            InlineKeyboardButton(say("下单占用", "On order"), callback_data=f"stk:{int(StockDeductStrategy.DAILY)}"),
            InlineKeyboardButton(say("支付后占用", "After payment"), callback_data=f"stk:{int(StockDeductStrategy.MARKETING)}"),
        ],
    ]
    await show(update, say("请选择库存策略。", "Choose when stock is reserved."), InlineKeyboardMarkup(rows))
    return ASK_STRATEGY


@guarded("bot:merchant")
async def _strategy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    form = _form(context)
    query = update.callback_query
    if form is None or query is None or query.data is None:
        return await _abort(update, context)
    form["stock_deduct_strategy"] = callback_int(query.data, 1)
    await show(update, say("请发送商品说明。发送 - 表示留空。", "Send the description. Send - to leave it empty."))
    return ASK_DESC


@guarded("bot:merchant")
async def _description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    form = _form(context)
    message = update.effective_message
    if form is None or message is None or message.text is None:
        return await _abort(update, context)
    form["description"] = "" if message.text.strip() == "-" else message.text.strip()
    await show(update, say("请发送封面地址，http 或 https。发送 - 表示不设置。", "Send a cover URL, http or https. Send - to skip it."))
    return ASK_COVER


@guarded("bot:merchant")
async def _cover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    form = _form(context)
    message = update.effective_message
    if form is None or message is None or message.text is None:
        return await _abort(update, context)
    cover = None if message.text.strip() == "-" else message.text.strip()
    try:
        async with merchant_session(update, context) as session:
            product, _detail = await create_product(
                session,
                {
                    "category_id": int(form["category_id"]),
                    "name": str(form["name"]),
                    "cover_url": cover,
                    "currency": int(form["currency"]),
                    "price_cents": int(form["price_cents"]),
                    "delivery_method": int(form["delivery_method"]),
                    "stock_deduct_strategy": int(form["stock_deduct_strategy"]),
                    "stock": 0,
                    "sort_order": 0,
                    "is_active": True,
                    "description": str(form["description"]),
                },
            )
            product_id = product.id
    except BizException as exc:
        await show(update, esc(tr(exc.message)))
        return ASK_COVER
    context.user_data.pop("product_form", None)
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(say("添加规格", "Add option"), callback_data=f"ps:{product_id}")],
            [InlineKeyboardButton(say("返回商品", "Products"), callback_data="pg:1")],
        ]
    )
    await show(update, say("商品已创建。没有规格时按商品价格出售。卡密导入后才会增加可售库存。", "Product created. Without options it sells at the product price. Stock increases when cards are imported."), markup)
    return ConversationHandler.END


@guarded("bot:merchant")
async def _spec_begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    assert query is not None and query.data is not None
    if query.data.startswith("sn:"):
        context.user_data["spec_form"] = {"action": "rename", "spec_id": callback_int(query.data, 1)}
        await show(update, say("请发送新的规格名称。", "Send the new option name."))
        return ASK_SPEC_NAME
    if query.data.startswith("sp:"):
        context.user_data["spec_form"] = {"action": "price", "spec_id": callback_int(query.data, 1)}
        await show(update, say("请发送规格价格，单位是元，例如 9.99。", "Send the option price in yuan, for example 9.99."))
        return ASK_SPEC_PRICE
    context.user_data["spec_form"] = {"action": "create", "product_id": callback_int(query.data, 1)}
    await show(update, say("请发送规格名称。", "Send the option name."))
    return ASK_SPEC_NAME


@guarded("bot:merchant")
async def _spec_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    form = context.user_data.get("spec_form")
    message = update.effective_message
    if not isinstance(form, dict) or message is None or message.text is None:
        return await _abort(update, context)
    if form.get("action") == "rename":
        spec_id = int(form["spec_id"])
        try:
            async with merchant_session(update, context) as session:
                await update_spec(session, spec_id, {"name": message.text})
        except BizException as exc:
            await show(update, esc(tr(exc.message)))
            return ASK_SPEC_NAME
        context.user_data.pop("spec_form", None)
        await _open_spec(update, context, spec_id)
        return ConversationHandler.END
    form["name"] = message.text.strip()
    await show(update, say("请发送规格价格，单位是元，例如 9.99。", "Send the option price in yuan, for example 9.99."))
    return ASK_SPEC_PRICE


@guarded("bot:merchant")
async def _spec_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    form = context.user_data.get("spec_form")
    message = update.effective_message
    if not isinstance(form, dict) or message is None or message.text is None:
        return await _abort(update, context)
    try:
        price = parse_cents(message.text)
    except BizException as exc:
        await show(update, esc(tr(exc.message)))
        return ASK_SPEC_PRICE
    if form.get("action") == "price":
        spec_id = int(form["spec_id"])
        try:
            async with merchant_session(update, context) as session:
                await update_spec(session, spec_id, {"price_cents": price})
        except BizException as exc:
            await show(update, esc(tr(exc.message)))
            return ASK_SPEC_PRICE
        context.user_data.pop("spec_form", None)
        await _open_spec(update, context, spec_id)
        return ConversationHandler.END
    try:
        async with merchant_session(update, context) as session:
            await create_spec(
                session,
                int(form["product_id"]),
                {"name": str(form["name"]), "price_cents": price, "stock": 0, "sort_order": 0, "is_active": True},
            )
    except BizException as exc:
        await show(update, esc(tr(exc.message)))
        return ASK_SPEC_PRICE
    context.user_data.pop("spec_form", None)
    await show(update, say("规格已添加。", "Option added."), merchant_menu())
    return ConversationHandler.END


_EDIT_PROMPTS = {
    "name": ("请发送新的商品名称。", "Send the new product name."),
    "price": ("请发送价格，单位是元，例如 9.99。", "Send the price in yuan, for example 9.99."),
    "description": ("请发送新的商品说明。发送 - 清空。", "Send the new description. Send - to clear it."),
    "cover": ("请发送封面地址，http 或 https。发送 - 表示去掉。", "Send a cover URL, http or https. Send - to remove it."),
    "sort": ("请发送排序，0 到 10000。", "Send a sort order from 0 to 10000."),
    "remark": ("请发送内部备注。发送 - 清空。", "Send the internal note. Send - to clear it."),
}


@guarded("bot:merchant")
async def _edit_begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    assert query is not None and query.data is not None
    action = {
        "p1": "name",
        "p2": "price",
        "pd": "description",
        "pu": "cover",
        "pq": "sort",
        "pm": "remark",
    }[query.data.split(":", 1)[0]]
    context.user_data["edit_form"] = {"action": action, "product_id": callback_int(query.data, 1)}
    await show(update, say(*_EDIT_PROMPTS[action]))
    return ASK_EDIT_DESC


@guarded("bot:merchant")
async def _edit_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    form = context.user_data.get("edit_form")
    message = update.effective_message
    if not isinstance(form, dict) or message is None or message.text is None:
        return await _abort(update, context)
    product_id = int(form["product_id"])
    raw = message.text.strip()
    action = str(form["action"])
    try:
        payload = _edit_payload(action, raw)
        async with merchant_session(update, context) as session:
            await update_product(session, product_id, payload)
    except BizException as exc:
        await show(update, esc(tr(exc.message)))
        return ASK_EDIT_DESC
    context.user_data.pop("edit_form", None)
    await _edit(update, context, product_id)
    return ConversationHandler.END


def _edit_payload(action: str, raw: str) -> dict:
    if action == "name":
        return {"name": raw}
    if action == "price":
        return {"price_cents": parse_cents(raw)}
    if action == "description":
        return {"description": "" if raw == "-" else raw}
    if action == "cover":
        return {"cover_url": None if raw == "-" else raw}
    if action == "sort":
        return {"sort_order": parse_int(raw, 0, 10_000, "排序超出范围")}
    if action == "remark":
        return {"internal_remark": None if raw == "-" else raw}
    raise BizException("参数无效", code=BizCode.BAD_REQUEST)


async def _take_text(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str, prompt: str, nxt: int):
    form = _form(context)
    message = update.effective_message
    if form is None or message is None or message.text is None or not message.text.strip():
        await show(update, say("内容不能为空。", "Content cannot be empty."))
        return ASK_NAME if key == "name" else nxt
    form[key] = message.text.strip()
    await show(update, prompt)
    return nxt


def _form(context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    form = context.user_data.get("product_form")
    if not isinstance(form, dict):
        return None
    return form


def _fallbacks():
    return [
        CommandHandler("cancel", _abort),
        CommandHandler("start", _abort),
        CallbackQueryHandler(_abort, pattern=r"^mm:"),
        CallbackQueryHandler(remind),
    ]


@guarded("bot:merchant")
async def _abort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("product_form", None)
    context.user_data.pop("spec_form", None)
    context.user_data.pop("edit_form", None)
    await show(update, say("已返回商家后台。", "Back at the merchant desk."), merchant_menu())
    return ConversationHandler.END


async def _actor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with merchant_session(update, context):
        return
