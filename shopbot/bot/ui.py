"""聊天回复、翻页按钮和异常提示。"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import ContextTypes, ConversationHandler

from shopbot.bot.formatting import clip_html, esc
from shopbot.bot.i18n import say, tr, use_language
from shopbot.bot.identity import cached_buyer_id, cached_language, open_buyer, open_merchant
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException
from shopbot.system.audit.context import bind_request, reset_request

logger = logging.getLogger("shopbot.bot")

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]


def buyer_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(say("选购", "Shop"), callback_data="m:shop"),
                InlineKeyboardButton(say("钱包", "Wallet"), callback_data="m:wallet"),
            ],
            [
                InlineKeyboardButton(say("订单", "Orders"), callback_data="m:orders"),
                InlineKeyboardButton(say("充值", "Top up"), callback_data="m:recharge"),
            ],
            [InlineKeyboardButton(say("语言", "Language"), callback_data="m:lang")],
        ]
    )


def buyer_reply_keyboard() -> ReplyKeyboardMarkup:
    """输入框上方的快捷键。点下去等于发出按钮上的文字。

    不保持常驻。点进输入框时由输入法盖住；收起后，客户端在输入框旁显示键盘图标，用来再打开它。
    """
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(say("选购", "Shop")), KeyboardButton(say("钱包", "Wallet"))],
            [KeyboardButton(say("订单", "Orders")), KeyboardButton(say("充值", "Top up"))],
            [KeyboardButton(say("语言", "Language"))],
        ],
        resize_keyboard=True,
        is_persistent=False,
    )


def merchant_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(say("分类", "Categories"), callback_data="mm:cat"),
                InlineKeyboardButton(say("商品", "Products"), callback_data="mm:prod"),
            ],
            [
                InlineKeyboardButton(say("卡密", "Cards"), callback_data="mm:card"),
                InlineKeyboardButton(say("待发货", "To deliver"), callback_data="mm:ship"),
            ],
            [
                InlineKeyboardButton(say("审计", "Audit"), callback_data="mm:audit"),
                InlineKeyboardButton(say("支付渠道", "Payments"), callback_data="mm:pay"),
            ],
            [InlineKeyboardButton(say("店铺设置", "Shop settings"), callback_data="mm:set")],
            [InlineKeyboardButton(say("语言", "Language"), callback_data="mm:lang")],
        ]
    )


def pair(buttons: list[InlineKeyboardButton]) -> list[list[InlineKeyboardButton]]:
    return rows_of(buttons, 2)


def rows_of(buttons: list[InlineKeyboardButton], width: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[i : i + width] for i in range(0, len(buttons), width)]


def pager(prefix: str, page: int, has_more: bool) -> list[InlineKeyboardButton]:
    row: list[InlineKeyboardButton] = []
    if page > 1:
        row.append(InlineKeyboardButton(say("上一页", "Previous"), callback_data=f"{prefix}:{page - 1}"))
    if has_more:
        row.append(InlineKeyboardButton(say("下一页", "Next"), callback_data=f"{prefix}:{page + 1}"))
    return row


def callback_int(data: str, index: int) -> int:
    parts = data.split(":")
    try:
        value = int(parts[index])
    except (IndexError, ValueError) as exc:
        raise BizException("参数无效", code=BizCode.BAD_REQUEST) from exc
    if value < 0:
        raise BizException("参数无效", code=BizCode.BAD_REQUEST)
    return value


async def remind(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    """表单还没填完时，其它按钮只提示继续或取消，避免客户端一直转圈。"""
    await show(update, say("当前还在填写中。请按提示继续，或发送 /cancel 返回。", "Finish this step, or send /cancel to go back."))


def guarded(scope: str) -> Callable[[Handler], Handler]:
    """私聊才处理。业务异常直接回复原因，其它异常不把细节发进聊天。"""

    def decorator(func: Handler) -> Handler:
        @wraps(func)
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
            if not _personal(update):
                return ConversationHandler.END
            query = update.callback_query
            person = update.effective_user
            use_language(cached_language(person.id) if person is not None else None)
            ack = asyncio.create_task(_answer(query)) if query is not None else None
            token = bind_request(scope, None)
            try:
                return await func(update, context)
            except BizException as exc:
                await tell(update, exc.message)
                return ConversationHandler.END
            except Exception:
                logger.exception("机器人处理失败")
                await tell(update, say("暂时无法处理，请稍后再试", "Please try again later"))
                return ConversationHandler.END
            finally:
                reset_request(token)
                if ack is not None:
                    await ack

        return wrapped

    return decorator


async def show(update: Update, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    await _send(update, clip_html(text), markup)


_CAPTION_LIMIT = 1000


async def show_cover(update: Update, text: str, url: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """详情有封面时发图片。文字消息改不成图片，所以另发一条，再删掉原来的文字。"""
    caption = clip_html(text, _CAPTION_LIMIT)
    query = update.callback_query
    if query is not None:
        await _answer(query)
    if query is not None and query.message is not None and query.message.photo:
        try:
            await _call(query.edit_message_caption, caption=caption, reply_markup=markup, parse_mode="HTML")
            return
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return
    message = update.effective_message
    if message is None:
        return
    try:
        await _call(message.reply_photo, url, caption=caption, reply_markup=markup, parse_mode="HTML")
    except BadRequest:
        await _send(update, caption, markup)
        return
    if query is not None and query.message is not None and not query.message.photo:
        try:
            await query.message.delete()
        except BadRequest:
            return


async def install_reply_keyboard(update: Update) -> None:
    """回复键盘和内联按钮不能挂在同一条消息上。发出后立刻删掉，键盘会留在输入框上方。"""
    message = update.effective_message
    if message is None:
        return
    try:
        sent = await message.reply_text("\u2060", reply_markup=buyer_reply_keyboard())
    except (BadRequest, NetworkError, TimedOut):
        return
    try:
        await sent.delete()
    except BadRequest:
        return


async def tell(update: Update, text: str) -> None:
    """业务异常按当前语言显示。正文来自异常原文，先转义再发出，避免把尖括号当成排版。"""
    await show(update, esc(tr(text)))


async def forget_message(update: Update) -> None:
    message = update.effective_message
    if message is None:
        return
    try:
        await message.delete()
    except BadRequest:
        return


@asynccontextmanager
async def merchant_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AsyncIterator[Any]:
    """鉴权和本次查询共用一个事务，避免点一次按钮连续占两次写锁。"""
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        await open_merchant(session, user.id, user.username)
        yield session


async def buyer_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """下单前拿到用户编号。这个进程里刚确认过的用户不再为了取编号单独开事务。"""
    user = update.effective_user
    assert user is not None
    found = cached_buyer_id(user.id)
    if found is not None:
        return found
    async with session_factory(context)() as session:
        buyer = await open_buyer(session, user.id, user.username)
        return buyer.id


def session_factory(context: ContextTypes.DEFAULT_TYPE):
    factory = context.application.bot_data.get("session_factory")
    if factory is None:
        raise BizException("暂时无法处理，请稍后再试", code=BizCode.INTERNAL_ERROR)
    return factory


def _personal(update: Update) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    return chat is not None and user is not None and chat.type == "private"


async def _send(update: Update, text: str, markup: InlineKeyboardMarkup | None) -> None:
    query = update.callback_query
    if query is not None:
        await _answer(query)
        try:
            await _call(query.edit_message_text, text, reply_markup=markup, parse_mode="HTML")
            return
        except BadRequest as exc:
            lowered = str(exc).lower()
            if "not modified" in lowered:
                return
            if "parse" in lowered:
                text = esc(text)
                try:
                    await _call(query.edit_message_text, text, reply_markup=markup, parse_mode="HTML")
                    return
                except BadRequest as again:
                    if "not modified" in str(again).lower():
                        return
        if query.message is not None:
            await _call(query.message.reply_text, text, reply_markup=markup, parse_mode="HTML")
            if query.message.photo:
                try:
                    await query.message.delete()
                except BadRequest:
                    return
            return
    message = update.effective_message
    if message is not None:
        await _call(message.reply_text, text, reply_markup=markup, parse_mode="HTML")


async def _call(method, *args, **kwargs) -> None:
    delay = 0.4
    for attempt in range(3):
        try:
            await method(*args, **kwargs)
            return
        except (NetworkError, TimedOut):
            if attempt == 2:
                raise
            await asyncio.sleep(delay)
            delay *= 2


async def _answer(query) -> None:
    try:
        await query.answer()
    except BadRequest:
        return
