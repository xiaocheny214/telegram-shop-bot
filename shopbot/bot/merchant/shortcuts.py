"""商家输入框上方的快捷键。

点一下等于发出按钮上的文字。这些文字如果在填写表单时出现，先退出那个表单，再打开对应页面。
回复键盘和内联按钮不能挂在同一条消息上，所以这里只负责认字和跳转。
"""

import re

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, filters

from shopbot.bot.language import merchant_language
from shopbot.bot.merchant.audit import show_audits
from shopbot.bot.merchant.cards import show_card_products
from shopbot.bot.merchant.catalog import show_categories
from shopbot.bot.merchant.payment import show_channels
from shopbot.bot.merchant.product import show_products
from shopbot.bot.merchant.settings import show_settings
from shopbot.bot.merchant.shipping import show_deliveries
from shopbot.bot.merchant.tasks import show_tasks
from shopbot.bot.merchant.users import show_users
from shopbot.bot.ui import MERCHANT_MENU_ITEMS, guarded, menu_aliases

_FORMS = (
    "merchant_category",
    "merchant_product",
    "merchant_spec",
    "merchant_product_edit",
    "merchant_cards",
    "merchant_user",
    "merchant_audit",
    "merchant_payment",
    "merchant_settings",
    "merchant_tasks",
)
_DRAFTS = (
    "category_form",
    "product_form",
    "spec_form",
    "edit_form",
    "card_form",
    "user_form",
    "audit_action",
    "channel_draft",
    "setting_field",
    "task_id",
)


def shortcut_filter():
    parts = [
        re.escape(alias)
        for zh, en, emoji, _callback, _action in MERCHANT_MENU_ITEMS
        for alias in menu_aliases(zh, en, emoji)
    ]
    return filters.Regex(rf"^({'|'.join(parts)})$")


def shortcut_action(text: str) -> str | None:
    raw = text.strip()
    for zh, en, emoji, _callback, action in MERCHANT_MENU_ITEMS:
        if raw in menu_aliases(zh, en, emoji):
            return action
    return None


@guarded("bot:merchant")
async def shortcut(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    action = shortcut_action("" if message is None or message.text is None else message.text)
    if action is None:
        return
    _leave_forms(update, context)
    if action == "categories":
        await show_categories(update, context, 1)
        return
    if action == "products":
        await show_products(update, context, 1)
        return
    if action == "cards":
        await show_card_products(update, context, 1)
        return
    if action == "deliveries":
        await show_deliveries(update, context, 1)
        return
    if action == "audit":
        await show_audits(update, context, 1)
        return
    if action == "payments":
        await show_channels(update, context, 1)
        return
    if action == "settings":
        await show_settings(update, context)
        return
    if action == "tasks":
        await show_tasks(update, context, 1)
        return
    if action == "users":
        await show_users(update, context, 1)
        return
    await merchant_language(update, context)


def _leave_forms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in _DRAFTS:
        context.user_data.pop(key, None)
    for name in _FORMS:
        handler = _conversation(context, name)
        if handler is not None:
            handler._conversations.pop(_key(update), None)


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
