"""分类的列表、新增、改名、排序、上下架和删除。"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from shopbot.bot.formatting import active_label, blocks, bold, button_label, esc, facts, page_slice, parse_int, shelf
from shopbot.bot.i18n import say, tr
from shopbot.bot.ui import callback_int, guarded, merchant_menu, merchant_session, pager, remind, show
from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.catalog.service import (
    create_category,
    delete_category,
    get_category,
    list_categories,
    update_category,
)

ASK_TEXT = 1


@guarded("bot:merchant")
async def categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = "" if query is None else query.data or ""
    if data.startswith("ct:"):
        await _toggle(update, context, callback_int(data, 1))
        return
    if data.startswith("cd:"):
        await _ask_delete(update, context, callback_int(data, 1))
        return
    if data.startswith("cy:"):
        await _delete(update, context, callback_int(data, 1))
        return
    if data.startswith("cv:"):
        await _open(update, context, callback_int(data, 1))
        return
    page = 1 if not data.startswith("cg:") else callback_int(data, 1)
    await show_categories(update, context, page)


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    async with merchant_session(update, context) as session:
        rows_data = await list_categories(session)
        chunk, has_more = page_slice(rows_data, page)
        buttons: list[list[InlineKeyboardButton]] = []
        cards: list[str] = []
        if not rows_data:
            text = blocks(bold(say("分类", "Categories")), esc(say("还没有分类。", "No categories yet.")))
        else:
            for category in chunk:
                cards.append(
                    shelf(
                        category.name,
                        say(
                            "{state} · 排序 {order}",
                            "{state} · sort {order}",
                            state=active_label(category.is_active),
                            order=category.sort_order,
                        ),
                    )
                )
                buttons.append([InlineKeyboardButton(button_label(category.name), callback_data=f"cv:{category.id}")])
            text = blocks(bold(say("分类", "Categories")), *cards)
        extra = pager("cg", page, has_more)
        if extra:
            buttons.append(extra)
        buttons.append([InlineKeyboardButton(say("新增分类", "New category"), callback_data="cn")])
        buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data="mm:home")])
    await show(update, text, InlineKeyboardMarkup(buttons))


async def _open(update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: int) -> None:
    async with merchant_session(update, context) as session:
        category = await get_category(session, category_id)
        name = category.name
        active = category.is_active
        sort_order = category.sort_order
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(say("改名", "Rename"), callback_data=f"cr:{category_id}"),
                InlineKeyboardButton(say("排序", "Sort"), callback_data=f"cs:{category_id}"),
            ],
            [
                InlineKeyboardButton(active_label(not active), callback_data=f"ct:{category_id}"),
                InlineKeyboardButton(say("删除", "Delete"), callback_data=f"cd:{category_id}"),
            ],
            [InlineKeyboardButton(say("返回分类", "Categories"), callback_data="cg:1")],
        ]
    )
    await show(
        update,
        blocks(
            bold(name),
            facts(
                (say("状态", "Status"), active_label(active)),
                (say("排序", "Sort"), str(sort_order)),
            ),
        ),
        markup,
    )


async def _toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: int) -> None:
    async with merchant_session(update, context) as session:
        category = await get_category(session, category_id)
        await update_category(session, category_id, {"is_active": not category.is_active})
    await _open(update, context, category_id)


async def _ask_delete(update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: int) -> None:
    async with merchant_session(update, context) as session:
        category = await get_category(session, category_id)
        name = category.name
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(say("确认删除", "Delete"), callback_data=f"cy:{category_id}")],
            [InlineKeyboardButton(say("返回", "Back"), callback_data=f"cv:{category_id}")],
        ]
    )
    await show(
        update,
        blocks(
            bold(name),
            esc(say("删除这个分类？分类下还有商品时会拒绝。", "Delete this category? It is refused while products remain.")),
        ),
        markup,
    )


async def _delete(update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: int) -> None:
    async with merchant_session(update, context) as session:
        await delete_category(session, category_id)
    await show_categories(update, context, 1)


def category_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(_begin, pattern=r"^(cn|cr:\d+|cs:\d+)$")],
        states={ASK_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, _text)]},
        fallbacks=[
            CommandHandler("cancel", _abort),
            CommandHandler("start", _abort),
            CallbackQueryHandler(_abort, pattern=r"^mm:"),
            CallbackQueryHandler(remind),
        ],
        allow_reentry=True,
        name="merchant_category",
    )


@guarded("bot:merchant")
async def _begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    assert query is not None and query.data is not None
    if query.data == "cn":
        context.user_data["category_form"] = {"action": "create"}
        await show(update, say("请发送分类名称。发送 /cancel 返回。", "Send the category name. Send /cancel to go back."))
        return ASK_TEXT
    action = "rename" if query.data.startswith("cr:") else "sort"
    context.user_data["category_form"] = {"action": action, "category_id": callback_int(query.data, 1)}
    prompt = (
        say("请发送新的分类名称。", "Send the new category name.")
        if action == "rename"
        else say("请发送排序，0 到 10000。", "Send a sort order from 0 to 10000.")
    )
    await show(update, prompt)
    return ASK_TEXT


@guarded("bot:merchant")
async def _text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    form = context.user_data.get("category_form")
    message = update.effective_message
    if not isinstance(form, dict) or message is None or message.text is None:
        return await _abort(update, context)
    try:
        async with merchant_session(update, context) as session:
            if form["action"] == "create":
                await create_category(session, {"name": message.text, "sort_order": 0, "is_active": True})
            elif form["action"] == "rename":
                await update_category(session, int(form["category_id"]), {"name": message.text})
            else:
                sort_order = parse_int(message.text, 0, 10_000, "排序超出范围")
                await update_category(session, int(form["category_id"]), {"sort_order": sort_order})
    except BizException as exc:
        await show(update, esc(tr(exc.message)))
        return ASK_TEXT
    context.user_data.pop("category_form", None)
    await show_categories(update, context, 1)
    return ConversationHandler.END


@guarded("bot:merchant")
async def _abort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("category_form", None)
    await show(update, say("已返回商家后台。", "Back at the merchant desk."), merchant_menu())
    return ConversationHandler.END

