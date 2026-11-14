"""把金额、状态和按钮文案转成聊天里能看的字。不放业务规则。"""

import html
import re
from datetime import datetime, timezone

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import (
    Currency,
    DeliveryMethod,
    EpayMethod,
    OrderStatus,
    PaymentProvider,
    StockDeductStrategy,
    UsdtNetwork,
    WalletDirection,
)
from shopbot.bot.i18n import say
from shopbot.framework.exceptions.biz import BizException

_MONEY = re.compile(r"^\d{1,8}(?:\.\d{1,2})?$")
_MESSAGE_LIMIT = 3900

_CURRENCY = {
    int(Currency.CNY): ("人民币", "CNY"),
    int(Currency.USD): ("美元", "USD"),
    int(Currency.USDT): ("USDT", "USDT"),
    int(Currency.GBP): ("英镑", "GBP"),
    int(Currency.EUR): ("欧元", "EUR"),
}
_PROVIDER = {
    int(PaymentProvider.BEPUSDT): ("BEpusdt", "BEpusdt"),
    int(PaymentProvider.EPAY): ("易支付", "Epay"),
    int(PaymentProvider.ALIPAY): ("支付宝", "Alipay"),
    int(PaymentProvider.WECHAT): ("微信", "WeChat"),
    int(PaymentProvider.STRIPE): ("Stripe", "Stripe"),
    int(PaymentProvider.WALLET): ("余额", "Balance"),
}
_EPAY = {
    int(EpayMethod.ALIPAY): ("支付宝", "Alipay"),
    int(EpayMethod.WECHAT): ("微信", "WeChat"),
}
_NETWORK = {
    int(UsdtNetwork.TRC20): "TRC20",
    int(UsdtNetwork.ERC20): "ERC20",
    int(UsdtNetwork.SOLANA): "Solana",
    int(UsdtNetwork.POLYGON): "Polygon",
}
_ORDER = {
    OrderStatus.PENDING: ("待支付", "Awaiting payment"),
    OrderStatus.PAID: ("已支付", "Paid"),
    OrderStatus.DELIVERED: ("已发货", "Delivered"),
    OrderStatus.EXPIRED: ("已过期", "Expired"),
    OrderStatus.CANCELLED: ("已取消", "Cancelled"),
    OrderStatus.FULFILL_FAILED: ("发货失败", "Delivery failed"),
}


def clip(text: str, limit: int = _MESSAGE_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def clip_html(text: str, limit: int = _MESSAGE_LIMIT) -> str:
    """截断时停在一行或一个标签结束的地方，避免把加粗标签切成两半。"""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit - 1)
    if cut < 0:
        cut = text.rfind(">", 0, limit - 1)
        if cut >= 0:
            cut += 1
    if cut < limit // 2:
        cut = limit - 1
    return text[:cut].rstrip() + "…"


def esc(text: object) -> str:
    return html.escape(str(text), quote=False)


def bold(text: object) -> str:
    return f"<b>{esc(text)}</b>"


def blocks(*parts: str) -> str:
    return "\n\n".join(part for part in parts if part)


def shelf(title: str, meta: str) -> str:
    """货架上一件东西：名称一行，价格或状态下一行。"""
    return f"{bold(title)}\n{esc(meta)}"


def marked(icon: str | None, name: str) -> str:
    """分类图标和名称。没有图标时只留名称。"""
    badge = (icon or "").strip()
    if not badge:
        return name
    return f"{badge} {name}"


def facts(*pairs: tuple[str, str]) -> str:
    return "\n".join(f"{bold(label)}  {esc(value)}" for label, value in pairs)


def anchor(url: str) -> str:
    raw = url.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        safe = html.escape(raw, quote=True)
        return f'<a href="{safe}">{safe}</a>'
    return esc(raw)


def fact_link(label: str, url: str) -> str:
    return f"{bold(label)}  {anchor(url)}"


def secret_block(text: str) -> str:
    return f"<pre>{esc(text)}</pre>"


def button_label(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) <= 30:
        return flat or say("未命名", "Untitled")
    return flat[:29] + "…"


def format_cents(cents: int, currency: int | None = None) -> str:
    sign = "-" if cents < 0 else ""
    amount = abs(cents)
    text = f"{sign}{amount // 100}.{amount % 100:02d}"
    if currency is None:
        return text
    return f"{text} {currency_label(currency)}"


def currency_label(currency: int) -> str:
    pair = _CURRENCY.get(currency)
    if pair is None:
        return say("未知货币", "Unknown currency")
    return say(*pair)


def provider_label(provider: int) -> str:
    pair = _PROVIDER.get(provider)
    if pair is None:
        return say("未知渠道", "Unknown channel")
    return say(*pair)


def epay_label(method: int | None) -> str:
    if method is None:
        return ""
    pair = _EPAY.get(method)
    if pair is None:
        return ""
    return say(*pair)


def network_label(network: int) -> str:
    name = _NETWORK.get(network)
    if name is None:
        return say("未知网络", "Unknown network")
    return name


def order_status_label(status: str) -> str:
    pair = _ORDER.get(status)
    if pair is None:
        return status
    return say(*pair)


def delivery_label(method: int) -> str:
    if method == int(DeliveryMethod.AUTO):
        return say("自动发货", "Automatic")
    if method == int(DeliveryMethod.MANUAL):
        return say("手动发货", "Manual")
    return say("未知发货方式", "Unknown delivery")


def strategy_label(strategy: int) -> str:
    if strategy == int(StockDeductStrategy.DAILY):
        return say("下单占用库存", "Reserve when ordered")
    if strategy == int(StockDeductStrategy.MARKETING):
        return say("支付后占用库存", "Reserve after payment")
    return say("未知库存策略", "Unknown stock rule")


def direction_label(direction: str) -> str:
    if direction == WalletDirection.CREDIT:
        return say("入账", "In")
    if direction == WalletDirection.DEBIT:
        return say("出账", "Out")
    return direction


def active_label(active: bool) -> str:
    return say("上架", "On sale") if active else say("下架", "Hidden")


def format_time(value: datetime | None) -> str:
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def parse_cents(text: str, *, allow_zero: bool = False) -> int:
    raw = text.strip()
    if not _MONEY.fullmatch(raw):
        raise BizException("金额无效", code=BizCode.BAD_REQUEST)
    whole, dot, frac = raw.partition(".")
    cents = int(whole) * 100
    if dot:
        cents += int(frac.ljust(2, "0"))
    if cents > 100_000_000 or cents < 0:
        raise BizException("金额超出范围", code=BizCode.BAD_REQUEST)
    if cents == 0 and not allow_zero:
        raise BizException("金额无效", code=BizCode.BAD_REQUEST)
    return cents


def parse_quantity(text: str) -> int:
    return parse_int(text, 1, 10_000, "购买数量无效")


def parse_int(text: str, low: int, high: int, message: str) -> int:
    raw = text.strip()
    if not raw.isdigit():
        raise BizException(message, code=BizCode.BAD_REQUEST)
    value = int(raw)
    if value < low or value > high:
        raise BizException(message, code=BizCode.BAD_REQUEST)
    return value


def page_slice[T](items: list[T], page: int, size: int = 5) -> tuple[list[T], bool]:
    if page < 1:
        page = 1
    start = (page - 1) * size
    chunk = items[start : start + size]
    return chunk, start + size < len(items)
