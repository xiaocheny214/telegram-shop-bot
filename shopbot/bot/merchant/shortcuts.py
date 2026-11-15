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
from shopbot.bot.ui import guarded

_LABELS = (
    ("分类", "Categories", "categories"),
    ("商品", "Products", "products"),
    ("卡密", "Cards", "cards"),
    ("待发货", "To deliver", "deliveries"),
    ("审计", "Audit", "audit"),
    ("支付渠道", "Payments", "payments"),
    ("店铺设置", "Shop settings", "settings"),
    ("语言", "Language", "language"),
)
_FORMS = (
    "merchant_category",
    "merchant_product",
    "merchant_spec",
    "merchant_description",
    "merchant_cards",
    "merchant_audit",
    "merchant_payment",
    "merchant_settings",
)
_DRAFTS = (
    "category_form",
    "product_form",
    "spec_form",
    "desc_form",
    "card_form",
    "audit_action",
    "channel_draft",
    "setting_field",
)


def shortcut_filter():
    parts = [re.escape(label) for zh, en, _action in _LABELS for label in (zh, en)]
    return filters.Regex(rf"^({'|'.join(parts)})$")


def shortcut_action(text: str) -> str | None:
    raw = text.strip()
    for zh, en, action in _LABELS:
        if raw == zh or raw == en:
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
