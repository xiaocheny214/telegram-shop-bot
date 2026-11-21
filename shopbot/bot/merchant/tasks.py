"""查看和修改定时任务。只能改间隔和启停，任务代码和名称保持原样。"""

import re

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from shopbot.bot.formatting import blocks, bold, esc, facts, format_time, page_slice
from shopbot.bot.i18n import say, tr
from shopbot.bot.ui import callback_int, guarded, merchant_menu, merchant_session, pager, remind, show
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException
from shopbot.system.models import ScheduledTask
from shopbot.system.scheduler.service import list_scheduled_tasks, update_scheduled_task

ASK_INTERVAL = 1
_PAGE_SIZE = 5
_INTEGER = re.compile(r"-?\d+")
_NAMES = {
    "close_expired_orders": ("关闭超时未支付订单", "Close unpaid orders"),
}


@guarded("bot:merchant")
async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = "" if query is None else query.data or ""
    if data.startswith("tt:"):
        await _toggle(update, context, callback_int(data, 1))
        return
    page = 1 if not data.startswith("tg:") else callback_int(data, 1)
    await show_tasks(update, context, page if page >= 1 else 1)


async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    async with merchant_session(update, context) as session:
        found = await list_scheduled_tasks(session)
        chunk, has_more = page_slice(found, page, _PAGE_SIZE)
        cards: list[str] = []
        buttons: list[list[InlineKeyboardButton]] = []
        if not found:
            cards.append(esc(say("还没有定时任务。", "No scheduled tasks yet.")))
        for task in chunk:
            cards.append(_card(task))
            buttons.append(
                [
                    InlineKeyboardButton(say("改间隔", "Interval"), callback_data=f"ti:{task.id}"),
                    InlineKeyboardButton(_toggle_label(task.is_active), callback_data=f"tt:{task.id}"),
                ]
            )
        extra = pager("tg", page, has_more)
        if extra:
            buttons.append(extra)
        buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data="mm:home")])
        text = blocks(bold(say("定时任务", "Scheduled tasks")), *cards)
    await show(update, text, InlineKeyboardMarkup(buttons))


def task_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(_begin, pattern=r"^ti:\d+$")],
        states={ASK_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, _save)]},
        fallbacks=[
            CommandHandler("cancel", _abort),
            CommandHandler("start", _abort),
            CallbackQueryHandler(_abort, pattern=r"^mm:"),
            CallbackQueryHandler(remind),
        ],
        allow_reentry=True,
        name="merchant_tasks",
    )


@guarded("bot:merchant")
async def _begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    assert query is not None and query.data is not None
    task_id = callback_int(query.data, 1)
    async with merchant_session(update, context) as session:
        await _stored(session, task_id)
    context.user_data["task_id"] = task_id
    await show(update, say("请发送任务间隔秒数，1 到 86400。", "Send the interval in seconds, from 1 to 86400."))
    return ASK_INTERVAL


@guarded("bot:merchant")
async def _save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.user_data.get("task_id")
    message = update.effective_message
    if type(task_id) is not int or message is None or message.text is None:
        return await _abort(update, context)
    try:
        async with merchant_session(update, context) as session:
            task = await _stored(session, task_id)
            code = task.code
            active = task.is_active
            await update_scheduled_task(
                session,
                code,
                {"interval_seconds": _interval(message.text), "is_active": active},
            )
    except BizException as exc:
        await show(update, esc(tr(exc.message)))
        return ASK_INTERVAL
    context.user_data.pop("task_id", None)
    await show_tasks(update, context, 1)
    return ConversationHandler.END


async def _toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int) -> None:
    async with merchant_session(update, context) as session:
        task = await _stored(session, task_id)
        code = task.code
        interval = task.interval_seconds
        active = not task.is_active
        await update_scheduled_task(session, code, {"interval_seconds": interval, "is_active": active})
    await show_tasks(update, context, 1)


def _card(task: ScheduledTask) -> str:
    ran = format_time(task.last_run_at) if task.last_run_at is not None else say("（空）", "(empty)")
    error = task.last_error or say("（空）", "(empty)")
    state = say("启用", "On") if task.is_active else say("停用", "Off")
    return blocks(
        bold(_title(task)),
        facts(
            (say("代码", "Code"), task.code),
            (say("间隔", "Interval"), say("{seconds} 秒", "{seconds} seconds", seconds=task.interval_seconds)),
            (say("状态", "Status"), state),
            (say("最近执行", "Last run"), ran),
            (say("最近错误", "Last error"), error),
        ),
    )


def _title(task: ScheduledTask) -> str:
    pair = _NAMES.get(task.code)
    if pair is None:
        return task.name
    return say(*pair)


def _toggle_label(active: bool) -> str:
    if active:
        return say("停用", "Disable")
    return say("启用", "Enable")


def _interval(text: str) -> object:
    raw = text.strip()
    if _INTEGER.fullmatch(raw):
        return int(raw)
    return raw


async def _stored(session: AsyncSession, task_id: int) -> ScheduledTask:
    found = await list_scheduled_tasks(session)
    for task in found:
        if task.id == task_id:
            return task
    raise BizException("定时任务不存在", code=BizCode.NOT_FOUND)


@guarded("bot:merchant")
async def _abort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("task_id", None)
    await show(update, say("已返回商家后台。", "Back at the merchant desk."), merchant_menu())
    return ConversationHandler.END
