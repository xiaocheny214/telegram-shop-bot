"""查看审计日志，可按动作名筛选。"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from shopbot.bot.formatting import blocks, bold, esc, format_time, shelf
from shopbot.bot.i18n import say, tr
from shopbot.bot.ui import callback_int, guarded, merchant_menu, merchant_session, pager, remind, show
from shopbot.framework.exceptions.biz import BizException
from shopbot.system.audit.service import list_audits

ASK_ACTION = 1


@guarded("bot:merchant")
async def audits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = "" if query is None else query.data or ""
    if data == "ax":
        context.user_data.pop("audit_action", None)
        await show_audits(update, context, 1)
        return
    page = 1 if not data.startswith("ag:") else callback_int(data, 1)
    await show_audits(update, context, page)


async def show_audits(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    action = context.user_data.get("audit_action")
    payload: dict = {"page": page, "page_size": 5}
    if isinstance(action, str) and action:
        payload["action"] = action
    async with merchant_session(update, context) as session:
        result = await list_audits(session, payload)
        cards: list[str] = []
        if action:
            cards.append(esc(say("动作：{action}", "Action: {action}", action=action)))
        if not result.items:
            cards.append(esc(say("没有记录。", "No records.")))
        for item in result.items:
            who = item.operator_telegram_id or "-"
            cards.append(
                shelf(
                    item.action,
                    say(
                        "{when} · {code} · {result} · {who}",
                        "{when} · {code} · {result} · {who}",
                        when=format_time(item.created_at),
                        code=item.status_code,
                        result=item.result,
                        who=who,
                    ),
                )
            )
        text = blocks(bold(say("审计日志", "Audit log")), *cards)
        buttons: list[list[InlineKeyboardButton]] = []
        extra = pager("ag", result.page, result.has_more)
        if extra:
            buttons.append(extra)
        buttons.append(
            [
                InlineKeyboardButton(say("按动作筛选", "Filter by action"), callback_data="af"),
                InlineKeyboardButton(say("清除筛选", "Clear filter"), callback_data="ax"),
            ]
        )
        buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data="mm:home")])
    await show(update, text, InlineKeyboardMarkup(buttons))


def audit_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(_begin, pattern=r"^af$")],
        states={ASK_ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, _save)]},
        fallbacks=[
            CommandHandler("cancel", _abort),
            CommandHandler("start", _abort),
            CallbackQueryHandler(_abort, pattern=r"^mm:"),
            CallbackQueryHandler(remind),
        ],
        allow_reentry=True,
        name="merchant_audit",
    )


@guarded("bot:merchant")
async def _begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show(update, say("请发送动作名，例如 order.place。发送 /cancel 返回。", "Send an action name, for example order.place. Send /cancel to go back."))
    return ASK_ACTION


@guarded("bot:merchant")
async def _save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or message.text is None:
        return await _abort(update, context)
    action = message.text.strip()
    if not action or len(action) > 128:
        await show(update, esc(say("动作名无效。", "Invalid action name.")))
        return ASK_ACTION
    context.user_data["audit_action"] = action
    try:
        await show_audits(update, context, 1)
    except BizException as exc:
        await show(update, esc(tr(exc.message)))
        return ASK_ACTION
    return ConversationHandler.END


@guarded("bot:merchant")
async def _abort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show(update, say("已返回商家后台。", "Back at the merchant desk."), merchant_menu())
    return ConversationHandler.END

