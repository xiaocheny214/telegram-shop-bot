"""买家和商家共用的语言选择。选择记在用户上，两个机器人读同一份。"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from shopbot.bot.formatting import blocks, bold, esc
from shopbot.bot.i18n import say, use_language
from shopbot.bot.identity import open_buyer, open_merchant, remember_language
from shopbot.bot.ui import buyer_menu, guarded, install_reply_keyboard, merchant_menu, session_factory, show
from shopbot.shop.user.service import set_user_language

_CODES = {"zh", "en"}


@guarded("bot:buyer")
async def buyer_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _choose(update, context, merchant=False)


@guarded("bot:merchant")
async def merchant_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _choose(update, context, merchant=True)


async def _choose(update: Update, context: ContextTypes.DEFAULT_TYPE, *, merchant: bool) -> None:
    query = update.callback_query
    data = "" if query is None or query.data is None else query.data
    prefix = "mm:lang" if merchant else "m:lang"
    home = "mm:home" if merchant else "m:home"
    menu = merchant_menu() if merchant else buyer_menu()
    if not data or data in {prefix, f"{prefix}:"}:
        rows = [
            [
                InlineKeyboardButton("中文", callback_data=f"{prefix}:zh"),
                InlineKeyboardButton("English", callback_data=f"{prefix}:en"),
            ],
            [InlineKeyboardButton(say("返回", "Back"), callback_data=home)],
        ]
        await show(update, blocks(bold(say("语言", "Language")), esc(say("请选择语言。", "Choose a language."))), InlineKeyboardMarkup(rows))
        return
    code = data.rsplit(":", 1)[-1]
    if code not in _CODES:
        await show(update, say("请选择语言。", "Choose a language."), menu)
        return
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        account = await (open_merchant if merchant else open_buyer)(session, user.id, user.username)
        await set_user_language(session, account.id, code)
    remember_language(user.id, code)
    use_language(code)
    text = (
        say("语言已切换为英文。", "Language set to English.")
        if code == "en"
        else say("语言已切换为中文。", "Language set to Chinese.")
    )
    await show(update, text, merchant_menu() if merchant else buyer_menu())
    if not merchant:
        await install_reply_keyboard(update)
