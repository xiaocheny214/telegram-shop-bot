"""批量导入卡密。回复只给成功和失败数量。"""

import asyncio
import tempfile
import uuid
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from shopbot.bot.formatting import blocks, bold, button_label, esc, format_cents, page_slice, shelf
from shopbot.bot.i18n import say
from shopbot.bot.ui import callback_int, guarded, merchant_menu, merchant_session, pager, remind, show
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.card.service import import_cards
from shopbot.shop.product.service import get_product, list_products, list_specs

WAIT_CONTENT = 1
_FILE_MAX = 1_048_576


@guarded("bot:merchant")
async def cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = "" if query is None else query.data or ""
    if data.startswith("kg:"):
        await show_card_products(update, context, callback_int(data, 1))
        return
    page = 1
    await show_card_products(update, context, page)


async def show_card_products(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    async with merchant_session(update, context) as session:
        products = await list_products(session)
        chunk, has_more = page_slice(products, page)
        cards = [
            shelf(product.name, format_cents(product.price_cents, product.currency))
            for product in chunk
        ]
        buttons = [[InlineKeyboardButton(button_label(product.name), callback_data=f"ki:{product.id}")] for product in chunk]
        if not products:
            text = blocks(bold(say("卡密", "Cards")), esc(say("还没有商品。", "No products yet.")))
        else:
            text = blocks(bold(say("卡密", "Cards")), esc(say("选择要导入的商品。", "Choose a product.")), *cards)
        extra = pager("kg", page, has_more)
        if extra:
            buttons.append(extra)
        buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data="mm:home")])
    await show(update, text, InlineKeyboardMarkup(buttons))


def card_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(_begin, pattern=r"^(ki:\d+|ks:\d+:\d+)$")],
        states={
            WAIT_CONTENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _text),
                MessageHandler(filters.Document.ALL, _file),
            ]
        },
        fallbacks=[
            CommandHandler("cancel", _abort),
            CommandHandler("start", _abort),
            CallbackQueryHandler(_abort, pattern=r"^mm:"),
            CallbackQueryHandler(remind),
        ],
        allow_reentry=True,
        name="merchant_cards",
    )


@guarded("bot:merchant")
async def _begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    assert query is not None and query.data is not None
    if query.data.startswith("ks:"):
        await _actor(update, context)
        context.user_data["card_form"] = {
            "product_id": callback_int(query.data, 1),
            "spec_id": callback_int(query.data, 2) or None,
        }
        if context.user_data["card_form"]["spec_id"] == 0:
            context.user_data["card_form"]["spec_id"] = None
        await show(update, say("请发送 txt 文件，或直接粘贴卡密，一行一条。发送 /cancel 返回。", "Send a txt file, or paste cards with one item per line. Send /cancel to go back."))
        return WAIT_CONTENT
    product_id = callback_int(query.data, 1)
    async with merchant_session(update, context) as session:
        _product, _detail = await get_product(session, product_id)
        specs = await list_specs(session, product_id)
    if specs:
        buttons = [
            [InlineKeyboardButton(button_label(spec.name), callback_data=f"ks:{product_id}:{spec.id}")] for spec in specs
        ]
        buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data="kg:1")])
        await show(update, say("这个商品有规格，请先选择规格。", "This product has options. Choose one first."), InlineKeyboardMarkup(buttons))
        return ConversationHandler.END
    context.user_data["card_form"] = {"product_id": product_id, "spec_id": None}
    await show(update, say("请发送 txt 文件，或直接粘贴卡密，一行一条。发送 /cancel 返回。", "Send a txt file, or paste cards with one item per line. Send /cancel to go back."))
    return WAIT_CONTENT


@guarded("bot:merchant")
async def _text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    form = context.user_data.get("card_form")
    message = update.effective_message
    if not isinstance(form, dict) or message is None or message.text is None:
        return await _abort(update, context)
    await _store(update, context, form, message.text, path=None)
    return ConversationHandler.END


@guarded("bot:merchant")
async def _file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    form = context.user_data.get("card_form")
    message = update.effective_message
    if not isinstance(form, dict) or message is None or message.document is None:
        return await _abort(update, context)
    document = message.document
    name = document.file_name or ""
    if not name.lower().endswith(".txt"):
        await show(update, say("只支持 .txt 文件。", "Only .txt files are supported."))
        return WAIT_CONTENT
    if document.file_size is not None and document.file_size > _FILE_MAX:
        await show(update, say("文件不能超过 1MB。", "The file must be 1MB or smaller."))
        return WAIT_CONTENT
    directory = Path(tempfile.gettempdir()) / "shopbot-cards"
    directory.mkdir(exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}.txt"
    try:
        telegram_file = await context.bot.get_file(document.file_id)
        await telegram_file.download_to_drive(str(path))
        await _store(update, context, form, None, path=path)
    finally:
        path.unlink(missing_ok=True)
    return ConversationHandler.END


async def _store(update: Update, context: ContextTypes.DEFAULT_TYPE, form: dict, text: str | None, path: Path | None) -> None:
    if path is not None:
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise BizException("文件编码必须是 UTF-8", code=BizCode.BAD_REQUEST) from exc
    if text is None:
        raise BizException("卡密内容不能为空", code=BizCode.BAD_REQUEST)
    payload = {"product_id": int(form["product_id"]), "spec_id": form["spec_id"], "text": text}
    async with merchant_session(update, context) as session:
        batch, _failures = await import_cards(session, payload)
        success = batch.success_count
        failure = batch.failure_count
    context.user_data.pop("card_form", None)
    await show(update, say("导入完成。成功 {success}，失败 {failure}。", "Import finished. {success} added, {failure} failed.", success=success, failure=failure), merchant_menu())


@guarded("bot:merchant")
async def _abort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("card_form", None)
    await show(update, say("已返回商家后台。", "Back at the merchant desk."), merchant_menu())
    return ConversationHandler.END


async def _actor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with merchant_session(update, context):
        return
