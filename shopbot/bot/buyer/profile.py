"""买家资料。内联按钮和回复键盘都打开这一页。"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from shopbot.bot.formatting import blocks, bold, facts, format_cents, format_time
from shopbot.bot.i18n import say
from shopbot.bot.identity import open_buyer
from shopbot.bot.ui import guarded, session_factory, show
from shopbot.framework.enums.model import Currency
from shopbot.shop.user.service import get_user_profile


@guarded("bot:buyer")
async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        buyer = await open_buyer(session, user.id, user.username)
        card = await get_user_profile(session, buyer.id)
    text = blocks(
        bold(say("我的资料", "Profile")),
        facts(
            (say("名字", "Name"), _name(user.first_name, card.username)),
            ("Telegram ID", str(card.telegram_id)),
            (say("余额", "Balance"), f"${format_cents(card.balance_usdt_cents, int(Currency.USDT))}"),
            (say("订单", "Orders"), str(card.order_count)),
            (say("注册时间", "Member since"), format_time(card.created_at)),
        ),
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton(say("返回主菜单", "Main menu"), callback_data="m:home")]])
    await show(update, text, markup)


def _name(first_name: str | None, username: str | None) -> str:
    display = (first_name or "").strip() or (username or "").strip()
    return display or say("朋友", "there")
