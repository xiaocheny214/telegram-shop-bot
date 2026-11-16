"""余额、流水、充值记录，以及创建充值单。"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from shopbot.bot.buyer.menu import start
from shopbot.bot.i18n import say, tr
from shopbot.bot.formatting import (
    blocks,
    bold,
    button_label,
    direction_label,
    esc,
    fact_link,
    facts,
    format_cents,
    format_time,
    network_label,
    order_status_label,
    parse_cents,
    provider_label,
    shelf,
)
from shopbot.bot.identity import open_buyer
from shopbot.bot.ui import buyer_id, buyer_menu, callback_int, guarded, pager, pair, remind, session_factory, show
from shopbot.framework.enums.model import ClientPlatform, Currency, OrderStatus, PaymentProvider, UsdtNetwork
from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.wallet.service import attach_recharge_client, get_balance, list_ledgers, list_user_recharges, recharge_wallet
from shopbot.system.payment.service import list_payment_channels

ASK_AMOUNT, ASK_CHANNEL, ASK_NETWORK = range(3)
logger = logging.getLogger(__name__)


@guarded("bot:buyer")
async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = "" if query is None else query.data or ""
    if data.startswith("r:"):
        await _recharges(update, context, callback_int(data, 1))
        return
    await _balance(update, context)


async def _balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        buyer = await open_buyer(session, user.id, user.username)
        balance = await get_balance(session, buyer.id)
        ledgers = await list_ledgers(session, buyer.id)
        parts = [
            bold(say("钱包", "Wallet")),
            bold(format_cents(balance, int(Currency.USDT))),
            bold(say("最近流水", "Recent activity")),
        ]
        if not ledgers:
            parts.append(esc(say("还没有流水。", "No activity yet.")))
        for ledger in ledgers[:8]:
            parts.append(
                shelf(
                    say(
                        "{when}  {direction}  {amount}",
                        "{when}  {direction}  {amount}",
                        when=format_time(ledger.created_at),
                        direction=direction_label(ledger.direction),
                        amount=format_cents(ledger.amount_usdt_cents, int(Currency.USDT)),
                    ),
                    ledger.reason or "-",
                )
            )
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(say("充值记录", "Top-ups"), callback_data="r:1"),
                InlineKeyboardButton(say("充值", "Top up"), callback_data="m:recharge"),
            ],
            [InlineKeyboardButton(say("返回主菜单", "Main menu"), callback_data="m:home")],
        ]
    )
    await show(update, blocks(*parts), markup)


async def _recharges(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        buyer = await open_buyer(session, user.id, user.username)
        result = await list_user_recharges(session, {"user_id": buyer.id, "page": page, "page_size": 5})
        cards: list[str] = []
        if not result.items:
            text = blocks(bold(say("充值记录", "Top-ups")), esc(say("还没有充值。", "No top-ups yet.")))
        else:
            for item in result.items:
                meta = say(
                    "{number} · {status}",
                    "{number} · {status}",
                    number=item.recharge_no,
                    status=order_status_label(item.status),
                )
                card = shelf(format_cents(item.amount_usdt_cents, int(Currency.USDT)), meta)
                if item.status == "pending" and item.pay_url:
                    card = blocks(card, fact_link(say("支付链接", "Payment link"), item.pay_url))
                cards.append(card)
            text = blocks(bold(say("充值记录", "Top-ups")), *cards)
        rows: list[list[InlineKeyboardButton]] = []
        extra = pager("r", result.page, result.has_more)
        if extra:
            rows.append(extra)
        rows.append([InlineKeyboardButton(say("返回钱包", "Wallet"), callback_data="m:wallet")])
    await show(update, text, InlineKeyboardMarkup(rows))


def recharge_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(_begin, pattern=r"^m:recharge$")],
        states={
            ASK_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, _amount)],
            ASK_CHANNEL: [CallbackQueryHandler(_channel, pattern=r"^rch:")],
            ASK_NETWORK: [CallbackQueryHandler(_network, pattern=r"^rnet:\d+$")],
        },
        fallbacks=[
            CommandHandler("cancel", _abort),
            CommandHandler("start", _abort),
            CallbackQueryHandler(_abort, pattern=r"^m:(?!recharge$).+"),
            CallbackQueryHandler(remind),
        ],
        allow_reentry=True,
        name="buyer_recharge",
    )


@guarded("bot:buyer")
async def _begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["recharge"] = {}
    await show(update, say("请输入要充值的 USDT 金额，例如 10 或 10.50。发送 /cancel 返回主菜单。", "Enter a USDT amount, for example 10 or 10.50. Send /cancel to return to the main menu."))
    return ASK_AMOUNT


@guarded("bot:buyer")
async def _amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or message.text is None:
        return await _abort(update, context)
    try:
        cents = parse_cents(message.text)
    except BizException as exc:
        await show(update, esc(tr(exc.message)))
        return ASK_AMOUNT
    context.user_data["recharge"] = {"amount_usdt_cents": cents}
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        await open_buyer(session, user.id, user.username)
        channels = [channel for channel in await list_payment_channels(session) if channel.is_active]
    if not channels:
        await show(update, say("还没有可用的支付渠道。", "No payment channel is available."), buyer_menu())
        return ConversationHandler.END
    rows = pair(
        [
            InlineKeyboardButton(button_label(provider_label(channel.provider)), callback_data=f"rch:{channel.id}")
            for channel in channels
        ]
    )
    rows.append([InlineKeyboardButton(say("返回主菜单", "Main menu"), callback_data="m:home")])
    await show(
        update,
        blocks(
            bold(say("充值", "Top up")),
            facts((say("金额", "Amount"), format_cents(cents, int(Currency.USDT)))),
            esc(say("选择支付渠道。", "Choose a payment channel.")),
        ),
        InlineKeyboardMarkup(rows),
    )
    return ASK_CHANNEL


@guarded("bot:buyer")
async def _channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    draft = context.user_data.get("recharge")
    assert query is not None and query.data is not None
    if not isinstance(draft, dict) or "amount_usdt_cents" not in draft:
        return await _abort(update, context)
    channel_id = callback_int(query.data, 1)
    user = update.effective_user
    assert user is not None
    async with session_factory(context)() as session:
        await open_buyer(session, user.id, user.username)
        channels = await list_payment_channels(session)
        channel = next((item for item in channels if item.id == channel_id and item.is_active), None)
    if channel is None:
        await show(update, say("支付渠道不可用。", "This payment channel is unavailable."), buyer_menu())
        return ConversationHandler.END
    draft["channel_id"] = channel.id
    if channel.provider == int(PaymentProvider.BEPUSDT):
        rows = [
            [
                InlineKeyboardButton(network_label(int(item)), callback_data=f"rnet:{int(item)}")
                for item in (UsdtNetwork.TRC20, UsdtNetwork.ERC20)
            ],
            [
                InlineKeyboardButton(network_label(int(item)), callback_data=f"rnet:{int(item)}")
                for item in (UsdtNetwork.SOLANA, UsdtNetwork.POLYGON)
            ],
        ]
        await show(update, say("请选择 USDT 网络。", "Choose a USDT network."), InlineKeyboardMarkup(rows))
        return ASK_NETWORK
    await _create(update, context, draft, None)
    context.user_data.pop("recharge", None)
    return ConversationHandler.END


@guarded("bot:buyer")
async def _network(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    draft = context.user_data.get("recharge")
    assert query is not None and query.data is not None
    if not isinstance(draft, dict):
        return await _abort(update, context)
    await _create(update, context, draft, callback_int(query.data, 1))
    context.user_data.pop("recharge", None)
    return ConversationHandler.END


async def _create(update: Update, context: ContextTypes.DEFAULT_TYPE, draft: dict, network: int | None) -> None:
    user_id = await buyer_id(update, context)
    recharge = await recharge_wallet(
        session_factory(context),
        {
            "user_id": user_id,
            "amount_usdt_cents": int(draft["amount_usdt_cents"]),
            "channel_id": int(draft["channel_id"]),
            "network": network,
        },
    )
    parts = [
        bold(say("充值单已创建", "Top-up created")),
        esc(say("到账后记入余额，不会发货。", "The balance is credited after payment. Nothing is delivered.")),
        facts(
            (say("单号", "No."), recharge.recharge_no),
            (say("金额", "Amount"), format_cents(recharge.amount_usdt_cents, int(Currency.USDT))),
            (say("状态", "Status"), order_status_label(OrderStatus.PENDING)),
        ),
    ]
    if recharge.pay_amount:
        parts.append(facts((say("实付", "Pay"), recharge.pay_amount)))
    if recharge.pay_target:
        parts.append(facts((say("收款", "Pay to"), recharge.pay_target)))
    if recharge.pay_url:
        parts.append(fact_link(say("支付链接", "Payment link"), recharge.pay_url))
    parts.append(esc(say("请在 {when} 前完成支付。", "Pay before {when}.", when=format_time(recharge.expires_at))))
    placed = await show(update, blocks(*parts), buyer_menu())
    if placed is None or placed.chat is None:
        return
    try:
        await attach_recharge_client(
            session_factory(context),
            recharge.id,
            ClientPlatform.TELEGRAM,
            chat_id=placed.chat.id,
            message_id=placed.message_id,
        )
    except Exception:
        logger.exception("充值来源没有记下 recharge_id=%s", recharge.id)


@guarded("bot:buyer")
async def _abort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("recharge", None)
    await start(update, context)
    return ConversationHandler.END
