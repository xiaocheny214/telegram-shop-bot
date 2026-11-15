"""注册商家机器人的命令、会话和按钮。"""

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from shopbot.bot.merchant.audit import audit_conversation, audits
from shopbot.bot.merchant.cards import card_conversation, cards
from shopbot.bot.merchant.catalog import categories, category_conversation
from shopbot.bot.language import merchant_language
from shopbot.bot.merchant.menu import route, start
from shopbot.bot.merchant.shortcuts import shortcut, shortcut_filter
from shopbot.bot.merchant.payment import channels, payment_conversation
from shopbot.bot.merchant.product import description_conversation, product_conversation, products, spec_conversation
from shopbot.bot.merchant.settings import settings_conversation
from shopbot.bot.merchant.shipping import shipping


def register_merchant(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(shortcut_filter() & filters.TEXT & ~filters.COMMAND, shortcut))
    application.add_handler(category_conversation())
    application.add_handler(product_conversation())
    application.add_handler(spec_conversation())
    application.add_handler(description_conversation())
    application.add_handler(card_conversation())
    application.add_handler(audit_conversation())
    application.add_handler(payment_conversation())
    application.add_handler(settings_conversation())
    application.add_handler(CallbackQueryHandler(merchant_language, pattern=r"^mm:lang"))
    application.add_handler(CallbackQueryHandler(route, pattern=r"^mm:"))
    application.add_handler(CallbackQueryHandler(categories, pattern=r"^(cg:\d+|ct:\d+|cd:\d+|cy:\d+|cv:\d+)$"))
    application.add_handler(CallbackQueryHandler(products, pattern=r"^(pg:\d+|pv:\d+|pt:\d+|px:\d+|py:\d+|pn:\d+)$"))
    application.add_handler(CallbackQueryHandler(cards, pattern=r"^kg:\d+$"))
    application.add_handler(CallbackQueryHandler(shipping, pattern=r"^(sg:\d+|sq:\d+|sy:\d+)$"))
    application.add_handler(CallbackQueryHandler(audits, pattern=r"^(ag:\d+|ax)$"))
    application.add_handler(CallbackQueryHandler(channels, pattern=r"^(qg:\d+|qt:\d+|qn)$"))
