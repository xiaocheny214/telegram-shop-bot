"""买家主菜单。"""

from telegram import Update
from telegram.ext import ContextTypes

from shopbot.bot.formatting import blocks, bold, esc
from shopbot.bot.i18n import say
from shopbot.bot.identity import open_buyer
from shopbot.bot.ui import buyer_menu, guarded, session_factory, show


@guarded("bot:buyer")
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        await open_buyer(session, user.id, user.username)
    await show(
        update,
        blocks(
            bold(say("欢迎光临", "Welcome")),
            esc(say("选一件商品。付款后，卡密发到这个聊天。", "Pick a product. The card arrives in this chat after payment.")),
        ),
        buyer_menu(),
    )
