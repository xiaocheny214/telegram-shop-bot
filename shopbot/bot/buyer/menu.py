"""买家主菜单。"""

from telegram import Update
from telegram.ext import ContextTypes

from shopbot.bot.formatting import blocks, bold, esc, facts, format_cents
from shopbot.bot.i18n import say
from shopbot.bot.identity import open_buyer
from shopbot.bot.ui import buyer_menu, buyer_reply_keyboard, guarded, install_reply_keyboard, session_factory, show
from shopbot.framework.enums.model import Currency
from shopbot.shop.setting.service import get_shop_setting


@guarded("bot:buyer")
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        buyer = await open_buyer(session, user.id, user.username)
        setting = await get_shop_setting(session)
        balance = buyer.balance_usdt_cents
        shop_name = setting.display_name
    await show(update, _home(user, shop_name, balance), buyer_menu())
    await install_reply_keyboard(update, buyer_reply_keyboard())


def _home(user, shop_name: str | None, balance: int) -> str:
    nickname = (user.first_name or "").strip() or (user.username or "").strip() or say("朋友", "there")
    title = shop_name.strip() if shop_name and shop_name.strip() else say("商店", "Shop")
    return blocks(
        bold(say("欢迎光临 {name} !", "Welcome to {name} !", name=title)),
        esc(say("你好！{name}", "Hello! {name}", name=nickname)),
        bold(say("账户信息", "Account Details")),
        facts(
            ("Telegram ID", str(user.id)),
            (say("余额", "Balance"), f"${format_cents(balance, int(Currency.USDT))}"),
        ),
        esc(say("请从下方选择 👇", "Choose below 👇")),
    )
