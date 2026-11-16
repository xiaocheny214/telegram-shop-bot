"""注册买家机器人的命令、会话和按钮。"""

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from shopbot.bot.buyer.catalog import browse, checkout_conversation
from shopbot.bot.buyer.menu import start
from shopbot.bot.buyer.orders import orders
from shopbot.bot.buyer.profile import profile
from shopbot.bot.buyer.shortcuts import shortcut, shortcut_filter
from shopbot.bot.buyer.wallet import recharge_conversation, wallet
from shopbot.bot.language import buyer_language


def register_buyer(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(shortcut_filter() & filters.TEXT & ~filters.COMMAND, shortcut))
    application.add_handler(checkout_conversation())
    application.add_handler(recharge_conversation())
    application.add_handler(CallbackQueryHandler(browse, pattern=r"^(m:home|m:shop|c:\d+|g:\d+:\d+|d:\d+)$"))
    application.add_handler(CallbackQueryHandler(orders, pattern=r"^(m:orders|o:\d+|od:\d+|ox:\d+)$"))
    application.add_handler(CallbackQueryHandler(wallet, pattern=r"^(m:wallet|r:\d+)$"))
    application.add_handler(CallbackQueryHandler(profile, pattern=r"^m:me$"))
    application.add_handler(CallbackQueryHandler(buyer_language, pattern=r"^m:lang"))
