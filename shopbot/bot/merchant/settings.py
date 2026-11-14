"""查看和修改店铺超时、待支付上限、感谢语和汇率。"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from shopbot.bot.formatting import blocks, bold, esc, facts, format_cents, parse_cents, parse_int
from shopbot.bot.i18n import say, tr
from shopbot.bot.ui import guarded, merchant_menu, merchant_session, remind, show
from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.setting.service import get_shop_setting, update_shop_setting

ASK_VALUE = 1
_PROMPTS = {
    "timeout": ("请发送订单超时秒数，60 到 86400。", "Send the order timeout in seconds, from 60 to 86400."),
    "pending": ("请发送同一买家的待支付订单上限，1 到 100。", "Send the pending-order limit per buyer, from 1 to 100."),
    "thanks": ("请发送感谢语。发送 - 清空。", "Send the thank-you note. Send - to clear it."),
    "usd": ("请发送 1 美元折合的人民币，例如 7.25。发送 0 表示未配置。", "Send how many CNY equal 1 USD, for example 7.25. Send 0 if unset."),
    "usdt": ("请发送 1 USDT 折合的人民币，例如 7.25。发送 0 表示未配置。", "Send how many CNY equal 1 USDT, for example 7.25. Send 0 if unset."),
    "gbp": ("请发送 1 英镑折合的人民币，例如 9.20。发送 0 表示未配置。", "Send how many CNY equal 1 GBP, for example 9.20. Send 0 if unset."),
    "eur": ("请发送 1 欧元折合的人民币，例如 7.80。发送 0 表示未配置。", "Send how many CNY equal 1 EUR, for example 7.80. Send 0 if unset."),
}
_FIELDS = {
    "timeout": "order_timeout_seconds",
    "pending": "max_pending_orders",
    "thanks": "thank_you_message",
    "usd": "usd_cny_cents",
    "usdt": "usdt_cny_cents",
    "gbp": "gbp_cny_cents",
    "eur": "eur_cny_cents",
}


async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with merchant_session(update, context) as session:
        setting = await get_shop_setting(session)
        thanks = setting.thank_you_message or say("（空）", "(empty)")
        text = blocks(
            bold(say("店铺设置", "Shop settings")),
            facts(
                (say("订单超时", "Order timeout"), say("{seconds} 秒", "{seconds} seconds", seconds=setting.order_timeout_seconds)),
                (say("待支付上限", "Pending limit"), str(setting.max_pending_orders)),
                (say("感谢语", "Thank-you"), thanks),
                (say("美元", "USD"), _rate(setting.usd_cny_cents)),
                ("USDT", _rate(setting.usdt_cny_cents)),
                (say("英镑", "GBP"), _rate(setting.gbp_cny_cents)),
                (say("欧元", "EUR"), _rate(setting.eur_cny_cents)),
            ),
        )
    buttons = [
        [
            InlineKeyboardButton(say("超时", "Timeout"), callback_data="ut:timeout"),
            InlineKeyboardButton(say("待支付上限", "Pending limit"), callback_data="ut:pending"),
        ],
        [InlineKeyboardButton(say("感谢语", "Thank-you"), callback_data="ut:thanks")],
        [
            InlineKeyboardButton(say("美元", "USD"), callback_data="ut:usd"),
            InlineKeyboardButton("USDT", callback_data="ut:usdt"),
        ],
        [
            InlineKeyboardButton(say("英镑", "GBP"), callback_data="ut:gbp"),
            InlineKeyboardButton(say("欧元", "EUR"), callback_data="ut:eur"),
        ],
        [InlineKeyboardButton(say("返回", "Back"), callback_data="mm:home")],
    ]
    await show(update, text, InlineKeyboardMarkup(buttons))


def settings_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(_begin, pattern=r"^ut:(timeout|pending|thanks|usd|usdt|gbp|eur)$")],
        states={ASK_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, _save)]},
        fallbacks=[
            CommandHandler("cancel", _abort),
            CommandHandler("start", _abort),
            CallbackQueryHandler(_abort, pattern=r"^mm:"),
            CallbackQueryHandler(remind),
        ],
        allow_reentry=True,
        name="merchant_settings",
    )


@guarded("bot:merchant")
async def _begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    assert query is not None and query.data is not None
    key = query.data.split(":", 1)[1]
    context.user_data["setting_field"] = key
    await show(update, say(*_PROMPTS[key]))
    return ASK_VALUE


@guarded("bot:merchant")
async def _save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get("setting_field")
    message = update.effective_message
    if not isinstance(key, str) or key not in _FIELDS or message is None or message.text is None:
        return await _abort(update, context)
    try:
        payload = {_FIELDS[key]: _parse(key, message.text)}
    except BizException as exc:
        await show(update, esc(tr(exc.message)))
        return ASK_VALUE
    try:
        async with merchant_session(update, context) as session:
            await update_shop_setting(session, payload)
    except BizException as exc:
        await show(update, esc(tr(exc.message)))
        return ASK_VALUE
    context.user_data.pop("setting_field", None)
    await show_settings(update, context)
    return ConversationHandler.END


def _parse(key: str, text: str):
    if key == "timeout":
        return parse_int(text, 60, 86_400, "订单超时必须在 60 到 86400 秒之间")
    if key == "pending":
        return parse_int(text, 1, 100, "待支付上限必须在 1 到 100 之间")
    if key == "thanks":
        raw = text.strip()
        return None if raw == "-" else raw
    return parse_cents(text, allow_zero=True)


def _rate(cents: int) -> str:
    if cents <= 0:
        return say("未配置", "Not set")
    return say("{amount} 元", "{amount} CNY", amount=format_cents(cents))


@guarded("bot:merchant")
async def _abort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("setting_field", None)
    await show(update, say("已返回商家后台。", "Back at the merchant desk."), merchant_menu())
    return ConversationHandler.END

