"""按供应商向支付渠道下单，结果折成统一的收款信息。"""

import hashlib
import json
import logging
from base64 import b64encode
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import Currency, EpayMethod, PaymentProvider
from shopbot.framework.exceptions.biz import BizException
from shopbot.system.models import AlipayChannel, BepusdtChannel, EpayChannel, PaymentChannel, WechatChannel
from shopbot.system.payment.gateway import ChannelPayment, usdt_trade_type
from shopbot.system.payment.notify import payment_notify_url

_FIAT = {
    Currency.CNY: "CNY",
    Currency.USD: "USD",
    Currency.GBP: "GBP",
    Currency.EUR: "EUR",
}
_EPAY_TYPE = {
    EpayMethod.ALIPAY: "alipay",
    EpayMethod.WECHAT: "wxpay",
}
_GATEWAY_TIMEOUT = 180
_GATEWAY_TIMEOUT_MAX = 3600
logger = logging.getLogger(__name__)


def open_payment(
    channel: PaymentChannel,
    detail: AlipayChannel | WechatChannel | EpayChannel | BepusdtChannel,
    *,
    order_no: str,
    subject: str,
    amount_cents: int,
    currency: int,
    network: int | None,
    timeout_seconds: int,
    cny_rates: dict[int, int],
) -> ChannelPayment:
    provider = PaymentProvider(channel.provider)
    if provider is PaymentProvider.BEPUSDT:
        if not isinstance(detail, BepusdtChannel):
            raise BizException("支付渠道配置不存在", code=BizCode.NOT_FOUND)
        return _bepusdt(detail, order_no=order_no, subject=subject, amount_cents=amount_cents, currency=currency, network=network, timeout_seconds=timeout_seconds)
    amount_cents = _to_cny_cents(amount_cents, currency, cny_rates)
    if provider is PaymentProvider.ALIPAY and isinstance(detail, AlipayChannel):
        return _alipay(detail, order_no=order_no, subject=subject, amount_cents=amount_cents)
    if provider is PaymentProvider.WECHAT and isinstance(detail, WechatChannel):
        return _wechat(detail, order_no=order_no, subject=subject, amount_cents=amount_cents)
    if provider is PaymentProvider.EPAY and isinstance(detail, EpayChannel):
        return _epay(channel, detail, order_no=order_no, subject=subject, amount_cents=amount_cents)
    raise BizException("支付渠道无效", code=BizCode.BAD_REQUEST)


def close_remote_payment(
    channel: PaymentChannel,
    detail: AlipayChannel | WechatChannel | EpayChannel | BepusdtChannel,
    channel_trade_no: str | None,
) -> None:
    """本地没记下收银台时，关掉渠道侧已经打开的交易。关单失败只记日志，不盖住原来的异常。"""
    if not channel_trade_no or PaymentProvider(channel.provider) is not PaymentProvider.BEPUSDT:
        return
    if not isinstance(detail, BepusdtChannel):
        return
    try:
        from shopbot.framework.client.payments import create_client

        client = create_client(detail.api_url, detail.api_token)
    except Exception:
        logger.exception("关闭 BEpusdt 订单失败 trade_id=%s", channel_trade_no)
        return
    _cancel_bepusdt(client, channel_trade_no)


def copy_payment_party(channel: PaymentChannel, detail: object) -> tuple[PaymentChannel, object]:
    """复制渠道配置，供写事务提交后在线程池里调用。复制体不挂在会话上。"""
    copied_channel = PaymentChannel(
        id=channel.id,
        provider=channel.provider,
        epay_method=channel.epay_method,
        sort_order=channel.sort_order,
        is_active=channel.is_active,
    )
    if isinstance(detail, EpayChannel):
        copied_detail: object = EpayChannel(
            channel_id=detail.channel_id,
            merchant_id=detail.merchant_id,
            merchant_key=detail.merchant_key,
            api_url=detail.api_url,
            notify_url=detail.notify_url,
            return_url=detail.return_url,
        )
    elif isinstance(detail, AlipayChannel):
        copied_detail = AlipayChannel(
            channel_id=detail.channel_id,
            app_id=detail.app_id,
            private_key=detail.private_key,
            alipay_public_key=detail.alipay_public_key,
            api_url=detail.api_url,
            notify_url=detail.notify_url,
        )
    elif isinstance(detail, WechatChannel):
        copied_detail = WechatChannel(
            channel_id=detail.channel_id,
            app_id=detail.app_id,
            merchant_id=detail.merchant_id,
            api_v3_key=detail.api_v3_key,
            cert_serial=detail.cert_serial,
            private_key_path=detail.private_key_path,
            notify_url=detail.notify_url,
        )
    elif isinstance(detail, BepusdtChannel):
        copied_detail = BepusdtChannel(
            channel_id=detail.channel_id,
            api_url=detail.api_url,
            api_token=detail.api_token,
            notify_url=detail.notify_url,
        )
    else:
        raise BizException("支付渠道配置不存在", code=BizCode.NOT_FOUND)
    return copied_channel, copied_detail


def payment_timeout_seconds(provider: int, timeout_seconds: int) -> int:
    """BEpusdt 只接受 180 到 3600 秒。本地过期时间用同一个值，避免收银台还开着库存先被释放。"""
    if provider == PaymentProvider.BEPUSDT:
        return min(_GATEWAY_TIMEOUT_MAX, max(_GATEWAY_TIMEOUT, timeout_seconds))
    return timeout_seconds


def _bepusdt(
    detail: BepusdtChannel,
    *,
    order_no: str,
    subject: str,
    amount_cents: int,
    currency: int,
    network: int | None,
    timeout_seconds: int,
) -> ChannelPayment:
    if network is None:
        raise BizException("请选择 USDT 网络", code=BizCode.BAD_REQUEST)
    fiat = "USD" if currency == Currency.USDT else _FIAT.get(Currency(currency))
    if fiat is None:
        raise BizException("该渠道不支持此货币", code=BizCode.BAD_REQUEST)
    timeout = payment_timeout_seconds(PaymentProvider.BEPUSDT, timeout_seconds)
    notify = detail.notify_url.strip() or payment_notify_url(detail.channel_id)
    if not notify:
        raise BizException("未配置公网地址", code=BizCode.BAD_REQUEST)
    request = {
        "order_id": order_no,
        "amount": _major_amount(amount_cents),
        "notify_url": notify,
        "trade_type": usdt_trade_type(network),
        "timeout": timeout,
        "fiat": fiat,
        "name": subject,
    }
    if currency == Currency.USDT:
        request["rate"] = 1
    try:
        from shopbot.framework.client.payments import create_client

        client = create_client(detail.api_url, detail.api_token)
        created = client.create_order(**request)
    except Exception as exc:
        raise BizException("支付渠道下单失败", code=BizCode.BAD_REQUEST) from exc
    try:
        return ChannelPayment(
            channel_trade_no=created.trade_id,
            pay_amount=str(created.actual_amount),
            pay_target=created.token or None,
            pay_url=created.payment_url or None,
        )
    except Exception:
        _cancel_bepusdt(client, getattr(created, "trade_id", None))
        raise


def _cancel_bepusdt(client: object, trade_id: str | None) -> None:
    cancel = getattr(client, "cancel_order", None)
    if not trade_id or cancel is None:
        return
    try:
        cancel(trade_id)
    except Exception:
        logger.exception("关闭 BEpusdt 订单失败 trade_id=%s", trade_id)


def _alipay(detail: AlipayChannel, *, order_no: str, subject: str, amount_cents: int) -> ChannelPayment:
    params = {
        "app_id": detail.app_id,
        "method": "alipay.trade.wap.pay",
        "format": "JSON",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "notify_url": detail.notify_url,
        "biz_content": json.dumps(
            {
                "out_trade_no": order_no,
                "total_amount": _yuan(amount_cents),
                "subject": subject,
                "product_code": "QUICK_WAP_WAY",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    params["sign"] = _rsa_sha256(params, detail.private_key)
    return ChannelPayment(
        channel_trade_no=order_no,
        pay_amount=_yuan(amount_cents),
        pay_target=None,
        pay_url=_join_query(detail.api_url, params),
    )


def _wechat(detail: WechatChannel, *, order_no: str, subject: str, amount_cents: int) -> ChannelPayment:
    path = "/v3/pay/transactions/native"
    body = json.dumps(
        {
            "appid": detail.app_id,
            "mchid": detail.merchant_id,
            "description": subject,
            "out_trade_no": order_no,
            "notify_url": detail.notify_url,
            "amount": {"total": amount_cents, "currency": "CNY"},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    timestamp = str(int(datetime.now().timestamp()))
    nonce = hashlib.sha256(f"{order_no}{timestamp}".encode()).hexdigest()
    message = f"POST\n{path}\n{timestamp}\n{nonce}\n{body}\n"
    signature = _rsa_sha256_message(message, _read_private_key(detail.private_key_path))
    authorization = (
        "WECHATPAY2-SHA256-RSA2048 "
        f'mchid="{detail.merchant_id}",nonce_str="{nonce}",signature="{signature}",'
        f'timestamp="{timestamp}",serial_no="{detail.cert_serial}"'
    )
    request = Request(
        "https://api.mch.weixin.qq.com" + path,
        data=body.encode(),
        headers={"Authorization": authorization, "Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
    except Exception as exc:
        raise BizException("支付渠道下单失败", code=BizCode.BAD_REQUEST) from exc
    code_url = payload.get("code_url")
    if not code_url:
        raise BizException("支付渠道下单失败", code=BizCode.BAD_REQUEST)
    return ChannelPayment(channel_trade_no=order_no, pay_amount=str(amount_cents), pay_target=None, pay_url=code_url)


def _epay(
    channel: PaymentChannel,
    detail: EpayChannel,
    *,
    order_no: str,
    subject: str,
    amount_cents: int,
) -> ChannelPayment:
    if channel.epay_method not in EpayMethod._value2member_map_:
        raise BizException("请选择易支付的支付宝或微信", code=BizCode.BAD_REQUEST)
    params = {
        "pid": detail.merchant_id,
        "type": _EPAY_TYPE[EpayMethod(channel.epay_method)],
        "out_trade_no": order_no,
        "notify_url": detail.notify_url,
        "return_url": detail.return_url,
        "name": subject,
        "money": _yuan(amount_cents),
    }
    params["sign"] = _md5_sign(params, detail.merchant_key)
    params["sign_type"] = "MD5"
    base = detail.api_url.rstrip("/")
    if not base.endswith("submit.php"):
        base += "/submit.php"
    return ChannelPayment(channel_trade_no=order_no, pay_amount=params["money"], pay_target=None, pay_url=_join_query(base, params))


_CNY_RATE_LABEL = {
    Currency.USD: "美元",
    Currency.USDT: "USDT",
    Currency.GBP: "英镑",
    Currency.EUR: "欧元",
}


def _to_cny_cents(amount_cents: int, currency: int, cny_rates: dict[int, int]) -> int:
    """按店铺汇率把商品金额折成人民币分。人民币商品不折算。"""
    if currency == Currency.CNY:
        return amount_cents
    label = _CNY_RATE_LABEL.get(Currency(currency)) if currency in Currency._value2member_map_ else None
    if label is None:
        raise BizException("该渠道不支持此货币", code=BizCode.BAD_REQUEST)
    rate = cny_rates.get(currency, 0)
    if type(rate) is not int or rate <= 0:
        raise BizException(f"未配置{label}汇率", code=BizCode.BAD_REQUEST)
    converted = (amount_cents * rate + 50) // 100
    if converted < 1:
        raise BizException("折算后金额过小", code=BizCode.BAD_REQUEST)
    return converted


def _major_amount(cents: int) -> int | float:
    if cents % 100 == 0:
        return cents // 100
    return cents / 100


def _yuan(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def _md5_sign(params: dict[str, str], key: str) -> str:
    text = "&".join(f"{name}={params[name]}" for name in sorted(params) if params[name])
    return hashlib.md5(f"{text}{key}".encode()).hexdigest()


def _rsa_sha256(params: dict[str, str], private_key: str) -> str:
    content = "&".join(f"{name}={params[name]}" for name in sorted(params) if params[name])
    return _rsa_sha256_message(content, private_key)


def _rsa_sha256_message(message: str, private_key: str) -> str:
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as exc:
        raise BizException("支付渠道下单失败", code=BizCode.BAD_REQUEST) from exc
    key = serialization.load_pem_private_key(_pem(private_key).encode(), password=None)
    signature = key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
    return b64encode(signature).decode()


def _pem(value: str) -> str:
    text = value.strip()
    if "BEGIN" in text:
        return text
    body = "\n".join(text[index : index + 64] for index in range(0, len(text), 64))
    return f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----"


def _read_private_key(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as file:
            return file.read()
    except OSError as exc:
        raise BizException("私钥文件不存在", code=BizCode.BAD_REQUEST) from exc


def _join_query(url: str, params: dict[str, str]) -> str:
    separator = "&" if "?" in url else "?"
    return url + separator + urlencode(params)
