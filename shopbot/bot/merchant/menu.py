"""商家主菜单。"""

from telegram import Update
from telegram.ext import ContextTypes

from shopbot.bot.formatting import blocks, bold, esc
from shopbot.bot.i18n import say
from shopbot.bot.ui import (
    guarded,
    install_reply_keyboard,
    merchant_menu,
    merchant_reply_keyboard,
    merchant_session,
    show,
)


@guarded("bot:merchant")
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _load(update, context)
    await show(
        update,
        blocks(
            bold(say("商家后台", "Merchant desk")),
            esc(say("管理分类、商品、卡密和待发货订单。", "Manage categories, products, cards, and orders waiting to ship.")),
        ),
        merchant_menu(),
    )
    if update.callback_query is None:
        await install_reply_keyboard(update, merchant_reply_keyboard())


@guarded("bot:merchant")
async def route(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = "" if query is None else query.data or ""
    if data in {"mm:home", ""}:
        await start(update, context)
        return
    if data == "mm:cat":
        from shopbot.bot.merchant.catalog import show_categories

        await show_categories(update, context, 1)
        return
    if data == "mm:prod":
        from shopbot.bot.merchant.product import show_products

        await show_products(update, context, 1)
        return
    if data == "mm:card":
        from shopbot.bot.merchant.cards import show_card_products

        await show_card_products(update, context, 1)
        return
    if data == "mm:ship":
        from shopbot.bot.merchant.shipping import show_deliveries

        await show_deliveries(update, context, 1)
        return
    if data == "mm:audit":
        from shopbot.bot.merchant.audit import show_audits

        await show_audits(update, context, 1)
        return
    if data == "mm:pay":
        from shopbot.bot.merchant.payment import show_channels

        await show_channels(update, context, 1)
        return
    if data == "mm:set":
        from shopbot.bot.merchant.settings import show_settings

        await show_settings(update, context)


async def _load(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with merchant_session(update, context):
        return
