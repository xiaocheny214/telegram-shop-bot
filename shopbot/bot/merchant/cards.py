"""卡密的列表、导入、修改和作废。占用中、已售和已作废的卡密只读。"""

import asyncio
import tempfile
import uuid
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from shopbot.bot.formatting import (
    blocks,
    bold,
    button_label,
    card_status_label,
    esc,
    facts,
    format_cents,
    mask_secret,
    page_slice,
    secret_block,
    shelf,
)
from shopbot.bot.i18n import say, tr
from shopbot.bot.ui import callback_int, guarded, merchant_menu, merchant_session, pager, remind, show
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import CardStatus
from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.card.service import discard_card, get_card, import_cards, page_cards, update_available_card
from shopbot.shop.product.service import get_product, list_products, list_specs

WAIT_CONTENT, WAIT_SECRET = range(2)
_FILE_MAX = 1_048_576
_STATUS = {
    "a": CardStatus.AVAILABLE,
    "r": CardStatus.RESERVED,
    "s": CardStatus.SOLD,
    "d": CardStatus.DISCARDED,
}


@guarded("bot:merchant")
async def cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = "" if query is None else query.data or ""
    if data.startswith("kg:"):
        await show_card_products(update, context, callback_int(data, 1))
        return
    if data.startswith("ki:"):
        await _choose_product(update, context, callback_int(data, 1))
        return
    if data.startswith("ks:"):
        await show_card_list(update, context, callback_int(data, 1), _spec(data, 2), CardStatus.AVAILABLE, 1)
        return
    if data.startswith("kl:"):
        await show_card_list(
            update,
            context,
            callback_int(data, 1),
            _spec(data, 2),
            _status(data),
            callback_int(data, 4),
        )
        return
    if data.startswith("kv:"):
        await _open_card(update, context, callback_int(data, 1))
        return
    if data.startswith("kx:"):
        await _ask_void(update, context, callback_int(data, 1))
        return
    if data.startswith("ky:"):
        await _void(update, context, callback_int(data, 1))
        return
    await show_card_products(update, context, 1)


async def show_card_products(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    async with merchant_session(update, context) as session:
        products = await list_products(session)
        chunk, has_more = page_slice(products, page)
        lines = [shelf(product.name, format_cents(product.price_cents, product.currency)) for product in chunk]
        buttons = [[InlineKeyboardButton(button_label(product.name), callback_data=f"ki:{product.id}")] for product in chunk]
        if not products:
            text = blocks(bold(say("卡密", "Cards")), esc(say("还没有商品。", "No products yet.")))
        else:
            text = blocks(bold(say("卡密", "Cards")), esc(say("选择要查看的商品。", "Choose a product.")), *lines)
        extra = pager("kg", page, has_more)
        if extra:
            buttons.append(extra)
        buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data="mm:home")])
    await show(update, text, InlineKeyboardMarkup(buttons))


async def show_card_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    product_id: int,
    spec_id: int | None,
    status: str,
    page: int,
) -> None:
    code = _code(status)
    spec_key = 0 if spec_id is None else spec_id
    async with merchant_session(update, context) as session:
        product, _detail = await get_product(session, product_id)
        spec_name = None
        if spec_id is not None:
            specs = await list_specs(session, product_id)
            spec = next((item for item in specs if item.id == spec_id), None)
            if spec is None:
                raise BizException("规格不存在", code=BizCode.NOT_FOUND)
            spec_name = spec.name
        found = await page_cards(session, product_id, spec_id, status, page)
        title = product.name if spec_name is None else f"{product.name} · {spec_name}"
        lines = [
            shelf(f"#{card.id}", f"{mask_secret(card.secret)} · {card_status_label(card.status)}")
            for card in found.items
        ]
        buttons = [[InlineKeyboardButton(f"#{card.id}", callback_data=f"kv:{card.id}")] for card in found.items]
        filters_row = [
            InlineKeyboardButton(card_status_label(item), callback_data=f"kl:{product_id}:{spec_key}:{_code(item)}:1")
            for item in (CardStatus.AVAILABLE, CardStatus.RESERVED, CardStatus.SOLD, CardStatus.DISCARDED)
        ]
        buttons.append(filters_row[:2])
        buttons.append(filters_row[2:])
        buttons.append([InlineKeyboardButton(say("导入", "Import"), callback_data=f"im:{product_id}:{spec_key}")])
        extra = pager(f"kl:{product_id}:{spec_key}:{code}", page, found.has_more)
        if extra:
            buttons.append(extra)
        back = f"ki:{product_id}" if spec_id is not None else "kg:1"
        buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data=back)])
        if not found.items:
            lines = [esc(say("这一状态下没有卡密。", "No cards in this state."))]
        text = blocks(bold(f"{say('卡密', 'Cards')} · {title} · {card_status_label(status)}"), *lines)
    await show(update, text, InlineKeyboardMarkup(buttons))


def card_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(_begin, pattern=r"^(im:\d+:\d+|ke:\d+)$")],
        states={
            WAIT_CONTENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _text),
                MessageHandler(filters.Document.ALL, _file),
            ],
            WAIT_SECRET: [MessageHandler(filters.TEXT & ~filters.COMMAND, _secret)],
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
    if query.data.startswith("ke:"):
        card_id = callback_int(query.data, 1)
        async with merchant_session(update, context) as session:
            card = await get_card(session, card_id)
            if card.status != CardStatus.AVAILABLE:
                raise BizException("只有未使用的卡密可以修改", code=BizCode.CONFLICT)
        context.user_data["card_form"] = {"action": "edit", "card_id": card_id}
        await show(update, say("请发送新的卡密原文。发送 /cancel 返回。", "Send the new card secret. Send /cancel to go back."))
        return WAIT_SECRET
    context.user_data["card_form"] = {
        "action": "import",
        "product_id": callback_int(query.data, 1),
        "spec_id": _spec(query.data, 2),
    }
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


@guarded("bot:merchant")
async def _secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    form = context.user_data.get("card_form")
    message = update.effective_message
    if not isinstance(form, dict) or message is None or message.text is None:
        return await _abort(update, context)
    card_id = int(form["card_id"])
    try:
        async with merchant_session(update, context) as session:
            await update_available_card(session, card_id, message.text)
    except BizException as exc:
        await show(update, esc(tr(exc.message)))
        return WAIT_SECRET
    context.user_data.pop("card_form", None)
    await _open_card(update, context, card_id)
    return ConversationHandler.END


async def _store(update: Update, context: ContextTypes.DEFAULT_TYPE, form: dict, text: str | None, path: Path | None) -> None:
    if path is not None:
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise BizException("文件编码必须是 UTF-8", code=BizCode.BAD_REQUEST) from exc
    if text is None:
        raise BizException("卡密内容不能为空", code=BizCode.BAD_REQUEST)
    product_id = int(form["product_id"])
    spec_id = form["spec_id"]
    payload = {"product_id": product_id, "spec_id": spec_id, "text": text}
    async with merchant_session(update, context) as session:
        batch, _failures = await import_cards(session, payload)
        success = batch.success_count
        failure = batch.failure_count
    context.user_data.pop("card_form", None)
    spec_key = 0 if spec_id is None else int(spec_id)
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(say("返回卡密", "Cards"), callback_data=f"kl:{product_id}:{spec_key}:a:1")],
            [InlineKeyboardButton(say("返回", "Back"), callback_data="mm:home")],
        ]
    )
    await show(
        update,
        say("导入完成。成功 {success}，失败 {failure}。", "Import finished. {success} added, {failure} failed.", success=success, failure=failure),
        markup,
    )


@guarded("bot:merchant")
async def _abort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("card_form", None)
    await show(update, say("已返回商家后台。", "Back at the merchant desk."), merchant_menu())
    return ConversationHandler.END


async def _choose_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    async with merchant_session(update, context) as session:
        _product, _detail = await get_product(session, product_id)
        specs = await list_specs(session, product_id)
    if specs:
        buttons = [[InlineKeyboardButton(button_label(spec.name), callback_data=f"ks:{product_id}:{spec.id}")] for spec in specs]
        buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data="kg:1")])
        await show(update, say("这个商品有规格，请先选择规格。", "This product has options. Choose one first."), InlineKeyboardMarkup(buttons))
        return
    await show_card_list(update, context, product_id, None, CardStatus.AVAILABLE, 1)


async def _open_card(update: Update, context: ContextTypes.DEFAULT_TYPE, card_id: int) -> None:
    async with merchant_session(update, context) as session:
        card = await get_card(session, card_id)
        secret = card.secret
        status = card.status
        product_id = card.product_id
        spec_id = card.spec_id
        order_id = card.order_id
    spec_key = 0 if spec_id is None else spec_id
    code = _code(status)
    rows = [
        (say("状态", "Status"), card_status_label(status)),
        (say("订单", "Order"), "-" if order_id is None else str(order_id)),
    ]
    buttons: list[list[InlineKeyboardButton]] = []
    if status == CardStatus.AVAILABLE:
        buttons.append(
            [
                InlineKeyboardButton(say("修改", "Edit"), callback_data=f"ke:{card_id}"),
                InlineKeyboardButton(say("作废", "Void"), callback_data=f"kx:{card_id}"),
            ]
        )
    buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data=f"kl:{product_id}:{spec_key}:{code}:1")])
    await show(
        update,
        blocks(bold(f"#{card_id} · {card_status_label(status)}"), facts(*rows), secret_block(secret)),
        InlineKeyboardMarkup(buttons),
    )


async def _ask_void(update: Update, context: ContextTypes.DEFAULT_TYPE, card_id: int) -> None:
    async with merchant_session(update, context) as session:
        card = await get_card(session, card_id)
        if card.status != CardStatus.AVAILABLE:
            raise BizException("只有未使用的卡密可以作废", code=BizCode.CONFLICT)
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(say("确认作废", "Void"), callback_data=f"ky:{card_id}")],
            [InlineKeyboardButton(say("返回", "Back"), callback_data=f"kv:{card_id}")],
        ]
    )
    await show(update, blocks(bold(say("作废卡密", "Void card")), esc(say("作废后不能再出售，也不能改回可用。", "A voided card cannot be sold or made available again."))), markup)


async def _void(update: Update, context: ContextTypes.DEFAULT_TYPE, card_id: int) -> None:
    async with merchant_session(update, context) as session:
        await discard_card(session, card_id)
    await _open_card(update, context, card_id)


def _spec(data: str, index: int) -> int | None:
    value = callback_int(data, index)
    if value == 0:
        return None
    return value


def _status(data: str) -> str:
    parts = data.split(":")
    try:
        code = parts[3]
    except IndexError as exc:
        raise BizException("参数无效", code=BizCode.BAD_REQUEST) from exc
    status = _STATUS.get(code)
    if status is None:
        raise BizException("卡密状态无效", code=BizCode.BAD_REQUEST)
    return status


def _code(status: str) -> str:
    for code, value in _STATUS.items():
        if value == status:
            return code
    raise BizException("卡密状态无效", code=BizCode.BAD_REQUEST)
