"""配置支付渠道。密钥只保存，不在回复里重复。"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from shopbot.bot.formatting import blocks, bold, button_label, epay_label, esc, page_slice, provider_label, shelf
from shopbot.bot.i18n import say, tr
from shopbot.bot.ui import (
    callback_int,
    forget_message,
    guarded,
    merchant_menu,
    merchant_session,
    pager,
    pair,
    remind,
    show,
)
from shopbot.framework.enums.model import EpayMethod, PaymentProvider
from shopbot.framework.exceptions.biz import BizException
from shopbot.system.models import AlipayChannel, BepusdtChannel, EpayChannel, WechatChannel
from shopbot.system.payment.service import (
    create_payment_channel,
    get_payment_channel,
    list_payment_channels,
    update_payment_channel,
)

ASK_METHOD, ASK_VALUE = range(2)
_SECRET_FIELDS = {"private_key", "alipay_public_key", "api_v3_key", "merchant_key", "api_token"}
_PROMPT_EN = {
    "请发送异步回调地址，必须是 https。": "Send the notify URL. It must be https.",
    "请发送应用编号。": "Send the app id.",
    "请发送应用私钥。这条消息会被删掉，机器人不会再显示它。": "Send the app private key. This message will be deleted and not shown again.",
    "请发送支付宝公钥。这条消息会被删掉，机器人不会再显示它。": "Send the Alipay public key. This message will be deleted and not shown again.",
    "请发送网关地址，必须是 https。": "Send the gateway URL. It must be https.",
    "请发送 APPID。": "Send the APPID.",
    "请发送商户号。": "Send the merchant id.",
    "请发送 APIv3 密钥。这条消息会被删掉，机器人不会再显示它。": "Send the APIv3 key. This message will be deleted and not shown again.",
    "请发送证书序列号。": "Send the certificate serial number.",
    "请发送服务器上的私钥文件路径。": "Send the private-key file path on the server.",
    "请发送商户 ID。": "Send the merchant id.",
    "请发送商户密钥。这条消息会被删掉，机器人不会再显示它。": "Send the merchant key. This message will be deleted and not shown again.",
    "请发送 api 地址，必须是 https。": "Send the API URL. It must be https.",
    "请发送同步跳转地址，必须是 https。": "Send the return URL. It must be https.",
    "请发送网关令牌。这条消息会被删掉，机器人不会再显示它。": "Send the gateway token. This message will be deleted and not shown again.",
}
_STEPS = {
    int(PaymentProvider.ALIPAY): [
        ("notify_url", "请发送异步回调地址，必须是 https。"),
        ("app_id", "请发送应用编号。"),
        ("private_key", "请发送应用私钥。这条消息会被删掉，机器人不会再显示它。"),
        ("alipay_public_key", "请发送支付宝公钥。这条消息会被删掉，机器人不会再显示它。"),
        ("api_url", "请发送网关地址，必须是 https。"),
    ],
    int(PaymentProvider.WECHAT): [
        ("notify_url", "请发送异步回调地址，必须是 https。"),
        ("app_id", "请发送 APPID。"),
        ("merchant_id", "请发送商户号。"),
        ("api_v3_key", "请发送 APIv3 密钥。这条消息会被删掉，机器人不会再显示它。"),
        ("cert_serial", "请发送证书序列号。"),
        ("private_key_path", "请发送服务器上的私钥文件路径。"),
    ],
    int(PaymentProvider.EPAY): [
        ("notify_url", "请发送异步回调地址，必须是 https。"),
        ("merchant_id", "请发送商户 ID。"),
        ("merchant_key", "请发送商户密钥。这条消息会被删掉，机器人不会再显示它。"),
        ("api_url", "请发送 api 地址，必须是 https。"),
        ("return_url", "请发送同步跳转地址，必须是 https。"),
    ],
    int(PaymentProvider.BEPUSDT): [
        ("api_url", "请发送网关地址，必须是 https。"),
        ("api_token", "请发送网关令牌。这条消息会被删掉，机器人不会再显示它。"),
    ],
}


@guarded("bot:merchant")
async def channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = "" if query is None else query.data or ""
    if data == "qn":
        await _pick_provider(update, context)
        return
    if data.startswith("qt:"):
        await _toggle(update, context, callback_int(data, 1))
        return
    page = 1 if not data.startswith("qg:") else callback_int(data, 1)
    await show_channels(update, context, page)


async def show_channels(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    async with merchant_session(update, context) as session:
        channels_ = await list_payment_channels(session)
        chunk, has_more = page_slice(channels_, page)
        buttons: list[list[InlineKeyboardButton]] = []
        cards: list[str] = []
        if not channels_:
            text = blocks(bold(say("支付渠道", "Payment channels")), esc(say("还没有支付渠道。", "No payment channels yet.")))
        else:
            for channel in chunk:
                extra = epay_label(channel.epay_method)
                name = provider_label(channel.provider)
                if extra:
                    name = f"{name} · {extra}"
                state = say("启用", "On") if channel.is_active else say("停用", "Off")
                cards.append(
                    shelf(
                        name,
                        say("{state} · 排序 {order}", "{state} · sort {order}", state=state, order=channel.sort_order),
                    )
                )
                toggle = say("停用 {name}", "Disable {name}", name=name) if channel.is_active else say("启用 {name}", "Enable {name}", name=name)
                buttons.append([InlineKeyboardButton(button_label(toggle), callback_data=f"qt:{channel.id}")])
            text = blocks(bold(say("支付渠道", "Payment channels")), *cards)
        extra_row = pager("qg", page, has_more)
        if extra_row:
            buttons.append(extra_row)
        buttons.append([InlineKeyboardButton(say("新增渠道", "New channel"), callback_data="qn")])
        buttons.append([InlineKeyboardButton(say("返回", "Back"), callback_data="mm:home")])
    await show(update, text, InlineKeyboardMarkup(buttons))


async def _pick_provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _actor(update, context)
    rows = pair(
        [InlineKeyboardButton(provider_label(provider), callback_data=f"qp:{provider}") for provider in _STEPS]
    )
    rows.append([InlineKeyboardButton(say("返回", "Back"), callback_data="qg:1")])
    await show(update, blocks(bold(say("新增渠道", "New channel")), esc(say("选择支付供应商。", "Choose a payment provider."))), InlineKeyboardMarkup(rows))


async def _toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id: int) -> None:
    async with merchant_session(update, context) as session:
        channel, detail = await get_payment_channel(session, channel_id)
        payload = _payload(channel, detail, active=not channel.is_active)
        await update_payment_channel(session, channel_id, payload)
    await show_channels(update, context, 1)


def payment_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(_begin, pattern=r"^qp:\d+$")],
        states={
            ASK_METHOD: [CallbackQueryHandler(_method, pattern=r"^qe:\d+$")],
            ASK_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, _value)],
        },
        fallbacks=[
            CommandHandler("cancel", _abort),
            CommandHandler("start", _abort),
            CallbackQueryHandler(_abort, pattern=r"^mm:"),
            CallbackQueryHandler(remind),
        ],
        allow_reentry=True,
        name="merchant_payment",
    )


@guarded("bot:merchant")
async def _begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    assert query is not None and query.data is not None
    provider = callback_int(query.data, 1)
    if provider not in _STEPS:
        await show(update, say("请选择支付宝、微信、易支付或 BEpusdt。", "Choose Alipay, WeChat, Epay, or BEpusdt."), merchant_menu())
        return ConversationHandler.END
    context.user_data["channel_draft"] = {"provider": provider, "values": {}, "step": 0}
    if provider == int(PaymentProvider.EPAY):
        rows = [
            [InlineKeyboardButton(say("支付宝", "Alipay"), callback_data=f"qe:{int(EpayMethod.ALIPAY)}")],
            [InlineKeyboardButton(say("微信", "WeChat"), callback_data=f"qe:{int(EpayMethod.WECHAT)}")],
        ]
        await show(update, say("请选择易支付方式。", "Choose the Epay method."), InlineKeyboardMarkup(rows))
        return ASK_METHOD
    await _prompt(update, context)
    return ASK_VALUE


@guarded("bot:merchant")
async def _method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data.get("channel_draft")
    query = update.callback_query
    if not isinstance(draft, dict) or query is None or query.data is None:
        return await _abort(update, context)
    draft["epay_method"] = callback_int(query.data, 1)
    await _prompt(update, context)
    return ASK_VALUE


@guarded("bot:merchant")
async def _value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data.get("channel_draft")
    message = update.effective_message
    if not isinstance(draft, dict) or message is None or message.text is None:
        return await _abort(update, context)
    steps = _STEPS[int(draft["provider"])]
    step = int(draft["step"])
    field, _prompt_text = steps[step]
    text = message.text.strip()
    if not text:
        await show(update, say("内容不能为空。", "Content cannot be empty."))
        return ASK_VALUE
    draft["values"][field] = text
    if field in _SECRET_FIELDS:
        await forget_message(update)
    draft["step"] = step + 1
    if draft["step"] >= len(steps):
        try:
            await _save(update, context, draft)
        except BizException as exc:
            await show(update, esc(tr(exc.message)))
            draft["step"] = step
            draft["values"].pop(field, None)
            await _prompt(update, context)
            return ASK_VALUE
        except Exception:
            context.user_data.pop("channel_draft", None)
            raise
        finally:
            if context.user_data.get("channel_draft") is draft and int(draft["step"]) >= len(steps):
                context.user_data.pop("channel_draft", None)
        return ConversationHandler.END
    await _prompt(update, context)
    return ASK_VALUE


async def _save(update: Update, context: ContextTypes.DEFAULT_TYPE, draft: dict) -> None:
    payload = {
        "provider": int(draft["provider"]),
        "sort_order": 0,
        "is_active": True,
        **draft["values"],
    }
    if "epay_method" in draft:
        payload["epay_method"] = int(draft["epay_method"])
    async with merchant_session(update, context) as session:
        channel, _detail = await create_payment_channel(session, payload)
        channel_id = channel.id
        provider = provider_label(int(draft["provider"]))
    await show(
        update,
        say(
            "支付渠道已保存。编号 {channel_id}，供应商 {provider}。密钥不会再次显示。",
            "Payment channel saved. Id {channel_id}, provider {provider}. Secrets are not shown again.",
            channel_id=channel_id,
            provider=provider,
        ),
        merchant_menu(),
    )


async def _prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    draft = context.user_data.get("channel_draft")
    if not isinstance(draft, dict):
        return
    _field, prompt = _STEPS[int(draft["provider"])][int(draft["step"])]
    await show(update, say(prompt, _PROMPT_EN.get(prompt, prompt)))


@guarded("bot:merchant")
async def _abort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("channel_draft", None)
    await show(update, say("已返回商家后台。密钥未保存。", "Back at the merchant desk. Secrets were not saved."), merchant_menu())
    return ConversationHandler.END


def _payload(channel, detail, *, active: bool) -> dict:
    data = {
        "provider": int(channel.provider),
        "sort_order": int(channel.sort_order),
        "is_active": active,
        "notify_url": detail.notify_url,
    }
    if int(channel.provider) == int(PaymentProvider.EPAY) and channel.epay_method is not None:
        data["epay_method"] = int(channel.epay_method)
    names = (
        "app_id",
        "private_key",
        "alipay_public_key",
        "api_url",
        "merchant_id",
        "api_v3_key",
        "cert_serial",
        "private_key_path",
        "merchant_key",
        "return_url",
        "api_token",
    )
    for name in names:
        if isinstance(detail, (AlipayChannel, WechatChannel, EpayChannel, BepusdtChannel)) and hasattr(detail, name):
            value = getattr(detail, name)
            if isinstance(value, str) and value:
                data[name] = value
    return data


async def _actor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with merchant_session(update, context):
        return
