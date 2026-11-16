"""买家输入框上方的快捷键。

点一下等于发出按钮上的文字。这些文字如果在结账或充值输入时出现，先退出那个表单，再打开对应页面。
回复键盘和内联按钮不能挂在同一条消息上，所以这里只负责认字和跳转。
"""

import re

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, filters

from shopbot.bot.buyer.catalog import browse
from shopbot.bot.buyer.orders import orders
from shopbot.bot.buyer.profile import profile
from shopbot.bot.buyer.wallet import ASK_AMOUNT
from shopbot.bot.buyer.wallet import _begin as begin_recharge
from shopbot.bot.buyer.wallet import wallet
from shopbot.bot.language import buyer_language
from shopbot.bot.ui import guarded

_LABELS = (
    ("选购", "Shop", "shop"),
    ("订单", "Orders", "orders"),
    ("钱包", "Wallet", "wallet"),
    ("充值", "Top up", "recharge"),
    ("语言", "Language", "language"),
    ("我的", "Profile", "profile"),
)
_FORMS = ("buyer_checkout", "buyer_recharge")


def shortcut_filter():
    parts = [re.escape(label) for zh, en, _action in _LABELS for label in (zh, en)]
    return filters.Regex(rf"^({'|'.join(parts)})$")


def shortcut_action(text: str) -> str | None:
    raw = text.strip()
    for zh, en, action in _LABELS:
        if raw == zh or raw == en:
            return action
    return None


@guarded("bot:buyer")
async def shortcut(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    action = shortcut_action("" if message is None or message.text is None else message.text)
    if action is None:
        return
    _leave_forms(update, context)
    if action == "shop":
        await browse(update, context)
        return
    if action == "orders":
        await orders(update, context)
        return
    if action == "wallet":
        await wallet(update, context)
        return
    if action == "language":
        await buyer_language(update, context)
        return
    if action == "profile":
        await profile(update, context)
        return
    entered = await begin_recharge(update, context)
    if entered == ASK_AMOUNT:
        _enter(update, context, "buyer_recharge", entered)


def _leave_forms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("checkout", None)
    context.user_data.pop("recharge", None)
    for name in _FORMS:
        handler = _conversation(context, name)
        if handler is not None:
            handler._conversations.pop(_key(update), None)


def _enter(update: Update, context: ContextTypes.DEFAULT_TYPE, name: str, state: object) -> None:
    handler = _conversation(context, name)
    if handler is None:
        return
    # 快捷键先于会话处理器执行，充值金额的下一步要靠这里把会话推进去。
    handler._conversations[_key(update)] = state


def _conversation(context: ContextTypes.DEFAULT_TYPE, name: str) -> ConversationHandler | None:
    for group in context.application.handlers.values():
        for handler in group:
            if isinstance(handler, ConversationHandler) and handler.name == name:
                return handler
    return None


def _key(update: Update) -> tuple[int, int]:
    chat = update.effective_chat
    user = update.effective_user
    assert chat is not None and user is not None
    return (chat.id, user.id)
