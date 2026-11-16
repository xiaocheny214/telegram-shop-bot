"""用户列表、停用和余额调整。余额变动写入流水，并记下原因和操作者。"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from shopbot.bot.formatting import (
    blocks,
    bold,
    button_label,
    esc,
    facts,
    format_cents,
    format_time,
    parse_signed_cents,
    role_label,
    shelf,
    user_state_label,
)
from shopbot.bot.i18n import say, tr
from shopbot.bot.identity import open_merchant
from shopbot.bot.ui import callback_int, guarded, merchant_menu, merchant_session, pager, remind, show
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import Currency, WalletDirection
from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.models import User
from shopbot.shop.user.service import (
    activate_user,
    deactivate_user,
    find_users,
    get_user,
    get_user_profile,
    page_users,
)
from shopbot.shop.wallet.service import adjust_balance, list_ledgers

ASK_FIND, ASK_AMOUNT, ASK_REASON = range(3)
_REASON_MAX = 64


@guarded("bot:merchant")
async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = "" if query is None else query.data or ""
    if data.startswith("uv:"):
        await _open(update, context, callback_int(data, 1))
        return
    if data.startswith("ud:"):
        await _deactivate(update, context, callback_int(data, 1))
        return
    if data.startswith("ue:"):
        await _activate(update, context, callback_int(data, 1))
        return
    if data.startswith("un:"):
        context.user_data.pop("user_form", None)
        await _open(update, context, callback_int(data, 1))
        return
    if data.startswith("ub:"):
        await _confirm_adjust(update, context, callback_int(data, 1))
        return
    page = 1 if not data.startswith("ug:") else callback_int(data, 1)
    await show_users(update, context, page)


async def show_users(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    async with merchant_session(update, context) as session:
        rows, has_more = await page_users(session, page)
        buttons: list[list[InlineKeyboardButton]] = []
        lines: list[str] = []
        if not rows:
            text = blocks(bold(say("用户", "Users")), esc(say("还没有用户。", "No users yet.")))
        else:
            for user in rows:
                lines.append(shelf(_label(user), _meta(user)))
                buttons.append([InlineKeyboardButton(button_label(_label(user)), callback_data=f"uv:{user.id}")])
            text = blocks(bold(say("用户", "Users")), *lines)
        extra = pager("ug", page, has_more)
        if extra:
            buttons.append(extra)
        buttons.append([InlineKeyboardButton(say("查找", "Find"), callback_data="uf")])
        buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data="mm:home")])
    await show(update, text, InlineKeyboardMarkup(buttons))


def user_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(_begin, pattern=r"^(uf|ua:\d+)$")],
        states={
            ASK_FIND: [MessageHandler(filters.TEXT & ~filters.COMMAND, _find)],
            ASK_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, _amount)],
            ASK_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, _reason)],
        },
        fallbacks=[
            CommandHandler("cancel", _abort),
            CommandHandler("start", _abort),
            CallbackQueryHandler(_abort, pattern=r"^mm:"),
            CallbackQueryHandler(remind),
        ],
        allow_reentry=True,
        name="merchant_user",
    )


@guarded("bot:merchant")
async def _begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    assert query is not None and query.data is not None
    if query.data == "uf":
        context.user_data["user_form"] = {"action": "find"}
        await show(update, say("请发送 Telegram 编号或用户名。发送 /cancel 返回。", "Send a Telegram id or username. Send /cancel to go back."))
        return ASK_FIND
    context.user_data["user_form"] = {"action": "adjust", "user_id": callback_int(query.data, 1)}
    await show(update, say("请发送调整金额，例如 +10.50 或 -3。发送 /cancel 返回。", "Send the adjustment, for example +10.50 or -3. Send /cancel to go back."))
    return ASK_AMOUNT


@guarded("bot:merchant")
async def _find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or message.text is None:
        return await _abort(update, context)
    async with merchant_session(update, context) as session:
        found = await find_users(session, message.text)
        shown = found[:5]
        labels = [(_label(user), user.id) for user in shown]
        extra = len(found) > 5
    if not labels:
        await show(update, esc(tr("用户不存在")))
        return ASK_FIND
    context.user_data.pop("user_form", None)
    if len(labels) == 1:
        await _open(update, context, labels[0][1])
        return ConversationHandler.END
    buttons = [[InlineKeyboardButton(button_label(label), callback_data=f"uv:{user_id}")] for label, user_id in labels]
    buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data="ug:1")])
    note = say("找到多个用户。", "Several users match.")
    if extra:
        note = say("找到多个用户。请换一个更精确的用户名。", "Several users match. Try a more specific username.")
    await show(update, blocks(bold(say("用户", "Users")), esc(note)), InlineKeyboardMarkup(buttons))
    return ConversationHandler.END


@guarded("bot:merchant")
async def _amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    form = context.user_data.get("user_form")
    message = update.effective_message
    if not isinstance(form, dict) or message is None or message.text is None:
        return await _abort(update, context)
    try:
        form["amount"] = parse_signed_cents(message.text)
    except BizException as exc:
        await show(update, esc(tr(exc.message)))
        return ASK_AMOUNT
    await show(update, say("请发送原因，最多 64 个字。", "Send the reason, up to 64 characters."))
    return ASK_REASON


@guarded("bot:merchant")
async def _reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    form = context.user_data.get("user_form")
    message = update.effective_message
    if not isinstance(form, dict) or message is None or message.text is None:
        return await _abort(update, context)
    reason = message.text.strip()
    try:
        _check_reason(reason)
    except BizException as exc:
        await show(update, esc(tr(exc.message)))
        return ASK_REASON
    form["reason"] = reason
    user_id = int(form["user_id"])
    amount = int(form["amount"])
    async with merchant_session(update, context) as session:
        user = await get_user(session, user_id)
        label = _label(user)
        balance = format_cents(user.balance_usdt_cents, int(Currency.USDT))
    sign = "+" if amount > 0 else "-"
    change = f"{sign}{format_cents(abs(amount), int(Currency.USDT))}"
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(say("确认调整", "Confirm"), callback_data=f"ub:{user_id}")],
            [InlineKeyboardButton(say("返回", "Back"), callback_data=f"un:{user_id}")],
        ]
    )
    await show(
        update,
        blocks(
            bold(say("调整余额", "Adjust balance")),
            bold(label),
            facts(
                (say("当前余额", "Current balance"), balance),
                (say("调整", "Change"), change),
                (say("原因", "Reason"), reason),
            ),
        ),
        markup,
    )
    return ConversationHandler.END


@guarded("bot:merchant")
async def _abort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("user_form", None)
    await show(update, say("已返回商家后台。", "Back at the merchant desk."), merchant_menu())
    return ConversationHandler.END


async def _open(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    async with merchant_session(update, context) as session:
        user = await get_user(session, user_id)
        profile = await get_user_profile(session, user.id)
        ledgers = (await list_ledgers(session, user.id))[:3]
        operator_ids = {row.operator_user_id for row in ledgers if row.operator_user_id is not None}
        operators: dict[int, User] = {}
        for operator_id in operator_ids:
            try:
                operators[operator_id] = await get_user(session, operator_id)
            except BizException:
                continue
        label = _label(user)
        rows = [
            (say("编号", "Id"), str(user.id)),
            (say("Telegram", "Telegram"), str(user.telegram_id)),
            (say("角色", "Role"), role_label(user.role)),
            (say("语言", "Language"), say("中文", "Chinese") if user.language == "zh" else say("英文", "English")),
            (say("状态", "Status"), user_state_label(user.is_active)),
            (say("余额", "Balance"), format_cents(user.balance_usdt_cents, int(Currency.USDT))),
            (say("订单", "Orders"), str(profile.order_count)),
            (say("注册", "Joined"), format_time(user.created_at)),
        ]
        history = [_ledger_line(row, operators) for row in ledgers]
        active = user.is_active
    if active:
        state_button = InlineKeyboardButton(say("停用", "Deactivate"), callback_data=f"ud:{user_id}")
    else:
        state_button = InlineKeyboardButton(say("启用", "Activate"), callback_data=f"ue:{user_id}")
    buttons = [
        [state_button, InlineKeyboardButton(say("调整余额", "Adjust balance"), callback_data=f"ua:{user_id}")],
        [InlineKeyboardButton(say("返回", "Back"), callback_data="ug:1")],
    ]
    parts = [bold(label), facts(*rows), bold(say("最近流水", "Recent ledger"))]
    if history:
        parts.extend(history)
    else:
        parts.append(esc(say("还没有流水。", "No ledger entries yet.")))
    await show(update, blocks(*parts), InlineKeyboardMarkup(buttons))


async def _deactivate(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    async with merchant_session(update, context) as session:
        actor_id = await _actor_id(update, session)
        await deactivate_user(session, user_id, actor_user_id=actor_id)
    await _open(update, context, user_id)


async def _activate(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    async with merchant_session(update, context) as session:
        await activate_user(session, user_id)
    await _open(update, context, user_id)


async def _confirm_adjust(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    form = context.user_data.get("user_form")
    if not isinstance(form, dict) or form.get("user_id") != user_id or "amount" not in form or "reason" not in form:
        await show(update, say("请重新填写调整金额和原因。", "Enter the amount and reason again."))
        return
    amount = int(form["amount"])
    reason = str(form["reason"])
    async with merchant_session(update, context) as session:
        actor_id = await _actor_id(update, session)
        await adjust_balance(
            session,
            user_id=user_id,
            amount_usdt_cents=amount,
            reason=reason,
            operator_user_id=actor_id,
        )
    context.user_data.pop("user_form", None)
    await _open(update, context, user_id)


async def _actor_id(update: Update, session) -> int:
    person = update.effective_user
    assert person is not None
    actor = await open_merchant(session, person.id, person.username)
    return actor.id


def _label(user: User) -> str:
    if user.username:
        return f"@{user.username}"
    return str(user.telegram_id)


def _meta(user: User) -> str:
    return say(
        "{telegram} · {role} · {state} · {balance}",
        "{telegram} · {role} · {state} · {balance}",
        telegram=str(user.telegram_id),
        role=role_label(user.role),
        state=user_state_label(user.is_active),
        balance=format_cents(user.balance_usdt_cents, int(Currency.USDT)),
    )


def _ledger_line(row, operators: dict[int, User]) -> str:
    sign = "+" if row.direction == WalletDirection.CREDIT else "-"
    amount = f"{sign}{format_cents(row.amount_usdt_cents, int(Currency.USDT))}"
    operator = say("系统", "System")
    if row.operator_user_id is not None:
        person = operators.get(row.operator_user_id)
        operator = _label(person) if person is not None else str(row.operator_user_id)
    return esc(f"{amount} · {row.reason} · {operator}")


def _check_reason(reason: str) -> None:
    if not reason:
        raise BizException("原因不能为空", code=BizCode.BAD_REQUEST)
    if len(reason) > _REASON_MAX:
        raise BizException("原因过长", code=BizCode.BAD_REQUEST)
    if any(ord(char) < 32 or ord(char) == 127 for char in reason):
        raise BizException("原因包含非法字符", code=BizCode.BAD_REQUEST)
