"""支付回调的两次核对。

先用渠道密钥验签，再向渠道查单。两次都通过，并且订单号和金额与本地下单时一致，才算支付成功。
BEpusdt 的查单是 POST /api/v1/pay/info。只信回调正文会被伪造通知刷走卡密。
"""

import json
from base64 import b64decode
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from urllib.request import Request, urlopen

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import PaymentProvider
from shopbot.framework.exceptions.biz import BizException
from shopbot.system.payment.drivers import _join_query, _md5_sign, _read_private_key, _rsa_sha256, _rsa_sha256_message

_SUCCESS_TEXT = {"TRADE_SUCCESS", "TRADE_FINISHED"}
Opener = Callable[..., object]


@dataclass(frozen=True)
class ChannelKeys:
    provider: int
    epay_method: int | None = None
    merchant_id: str = ""
    merchant_key: str = ""
    api_url: str = ""
    app_id: str = ""
    private_key: str = ""
    alipay_public_key: str = ""
    api_v3_key: str = ""
    cert_serial: str = ""
    private_key_path: str = ""
    api_token: str = ""


@dataclass(frozen=True)
class VerifiedPayment:
    order_no: str
    channel_trade_no: str
    channel_status: int
    paid_amount: str
    fiat_amount: str | None = None


def confirm_callback(
    keys: ChannelKeys,
    *,
    form: dict[str, str] | None,
    data: dict | None,
    opener: Opener = urlopen,
) -> VerifiedPayment:
    provider = PaymentProvider(keys.provider)
    if provider is PaymentProvider.EPAY:
        return _epay(keys, form or {}, opener)
    if provider is PaymentProvider.ALIPAY:
        return _alipay(keys, form or {}, opener)
    if provider is PaymentProvider.WECHAT:
        return _wechat(keys, data or {}, opener)
    if provider is PaymentProvider.BEPUSDT:
        return _bepusdt(keys, data or {}, opener)
    raise BizException("支付渠道无效", code=BizCode.BAD_REQUEST)


def _epay(keys: ChannelKeys, form: dict[str, str], opener: Opener) -> VerifiedPayment:
    _require_md5(form, keys.merchant_key)
    if form.get("trade_status") != "TRADE_SUCCESS":
        raise BizException("支付未成功", code=BizCode.CONFLICT)
    order_no = _text(form.get("out_trade_no"), "订单号无效")
    remote = _read_json(
        opener,
        Request(_epay_query_url(keys, order_no), method="GET"),
    )
    if str(remote.get("status")) != "1" or str(remote.get("out_trade_no") or "") != order_no:
        raise BizException("支付未确认", code=BizCode.CONFLICT)
    if not _same_money(remote.get("money"), form.get("money")):
        raise BizException("支付金额不符", code=BizCode.CONFLICT)
    return VerifiedPayment(
        order_no=order_no,
        channel_trade_no=_text(remote.get("trade_no") or form.get("trade_no"), "渠道交易号无效"),
        channel_status=1,
        paid_amount=str(remote.get("money")),
    )


def _alipay(keys: ChannelKeys, form: dict[str, str], opener: Opener) -> VerifiedPayment:
    _require_rsa(form, keys.alipay_public_key)
    if form.get("trade_status") not in _SUCCESS_TEXT:
        raise BizException("支付未成功", code=BizCode.CONFLICT)
    order_no = _text(form.get("out_trade_no"), "订单号无效")
    signed = _alipay_query_params(keys, order_no)
    raw = _read_text(opener, Request(_join_query(keys.api_url, signed), method="GET"))
    node = "alipay_trade_query_response"
    body = _json_object(raw, node)
    _require_rsa_message(body, _json_sign(raw), keys.alipay_public_key)
    payload = json.loads(body)
    if payload.get("trade_status") not in _SUCCESS_TEXT or str(payload.get("out_trade_no") or "") != order_no:
        raise BizException("支付未确认", code=BizCode.CONFLICT)
    if not _same_money(payload.get("total_amount"), form.get("total_amount")):
        raise BizException("支付金额不符", code=BizCode.CONFLICT)
    return VerifiedPayment(
        order_no=order_no,
        channel_trade_no=_text(payload.get("trade_no") or form.get("trade_no"), "渠道交易号无效"),
        channel_status=1,
        paid_amount=str(payload.get("total_amount")),
    )


def _wechat(keys: ChannelKeys, data: dict, opener: Opener) -> VerifiedPayment:
    paid = _decrypt_wechat(data, keys.api_v3_key)
    if paid.get("trade_state") != "SUCCESS":
        raise BizException("支付未成功", code=BizCode.CONFLICT)
    order_no = _text(paid.get("out_trade_no"), "订单号无效")
    amount = paid.get("amount") if isinstance(paid.get("amount"), dict) else {}
    notified = amount.get("total")
    path = f"/v3/pay/transactions/out-trade-no/{order_no}?mchid={keys.merchant_id}"
    timestamp = str(int(datetime.now().timestamp()))
    nonce = sha256(f"{order_no}{timestamp}".encode()).hexdigest()
    signature = _rsa_sha256_message(f"GET\n{path}\n{timestamp}\n{nonce}\n\n", _read_private_key(keys.private_key_path))
    authorization = (
        "WECHATPAY2-SHA256-RSA2048 "
        f'mchid="{keys.merchant_id}",nonce_str="{nonce}",signature="{signature}",'
        f'timestamp="{timestamp}",serial_no="{keys.cert_serial}"'
    )
    remote = _read_json(
        opener,
        Request(
            "https://api.mch.weixin.qq.com" + path,
            headers={"Authorization": authorization, "Accept": "application/json"},
            method="GET",
        ),
    )
    remote_amount = remote.get("amount") if isinstance(remote.get("amount"), dict) else {}
    if remote.get("trade_state") != "SUCCESS" or str(remote.get("out_trade_no") or "") != order_no:
        raise BizException("支付未确认", code=BizCode.CONFLICT)
    if str(remote_amount.get("total")) != str(notified):
        raise BizException("支付金额不符", code=BizCode.CONFLICT)
    return VerifiedPayment(
        order_no=order_no,
        channel_trade_no=_text(remote.get("transaction_id") or paid.get("transaction_id"), "渠道交易号无效"),
        channel_status=1,
        paid_amount=str(remote_amount.get("total")),
    )


def _bepusdt(keys: ChannelKeys, data: dict, opener: Opener) -> VerifiedPayment:
    from shopbot.framework.client.payments import create_client, trade_info_request

    try:
        if not create_client(keys.api_url, keys.api_token).verify_callback(data):
            raise BizException("回调签名无效", code=BizCode.CONFLICT)
        if int(data.get("status", 0)) != 2:
            raise BizException("支付未成功", code=BizCode.CONFLICT)
        order_no = _text(data.get("order_id"), "订单号无效")
        trade_no = _text(data.get("trade_id"), "渠道交易号无效")
        info = _bepusdt_info(_read_json(opener, trade_info_request(keys.api_url, trade_no)))
        if int(info.get("status", 0)) != 2:
            raise BizException("支付未确认", code=BizCode.CONFLICT)
        if _text(info.get("order_id"), "订单号无效") != order_no or _text(info.get("trade_id"), "渠道交易号无效") != trade_no:
            raise BizException("支付未确认", code=BizCode.CONFLICT)
        if not _same_money(info.get("actual_amount"), data.get("actual_amount")) or not _same_money(info.get("money"), data.get("amount")):
            raise BizException("支付金额不符", code=BizCode.CONFLICT)
    except BizException:
        raise
    except Exception as exc:
        raise BizException("支付未确认", code=BizCode.CONFLICT) from exc
    return VerifiedPayment(
        order_no=order_no,
        channel_trade_no=trade_no,
        channel_status=2,
        paid_amount=str(info.get("actual_amount")),
        fiat_amount=str(info.get("money")),
    )


def _bepusdt_info(payload: dict) -> dict:
    if str(payload.get("status_code")) != "200":
        raise BizException("支付未确认", code=BizCode.CONFLICT)
    info = payload.get("data")
    if not isinstance(info, dict):
        raise BizException("支付未确认", code=BizCode.CONFLICT)
    return info


def _alipay_query_params(keys: ChannelKeys, order_no: str) -> dict[str, str]:
    params = {
        "app_id": keys.app_id,
        "method": "alipay.trade.query",
        "format": "JSON",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "biz_content": json.dumps({"out_trade_no": order_no}, separators=(",", ":")),
    }
    params["sign"] = _rsa_sha256(params, keys.private_key)
    return params


def _epay_query_url(keys: ChannelKeys, order_no: str) -> str:
    base = keys.api_url.rstrip("/")
    if base.endswith("submit.php"):
        base = base[: -len("submit.php")].rstrip("/")
    return _join_query(
        base + "/api.php",
        {"act": "order", "pid": keys.merchant_id, "key": keys.merchant_key, "out_trade_no": order_no},
    )


def _require_md5(form: dict[str, str], key: str) -> None:
    signed = {name: value for name, value in form.items() if name not in {"sign", "sign_type"}}
    if not form.get("sign") or _md5_sign(signed, key).lower() != form["sign"].lower():
        raise BizException("回调签名无效", code=BizCode.CONFLICT)


def _require_rsa(form: dict[str, str], public_key: str) -> None:
    content = "&".join(
        f"{name}={form[name]}"
        for name in sorted(form)
        if name not in {"sign", "sign_type"} and form[name]
    )
    _require_rsa_message(content, form.get("sign") or "", public_key)


def _require_rsa_message(message: str, signature: str, public_key: str) -> None:
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as exc:
        raise BizException("支付未确认", code=BizCode.CONFLICT) from exc
    try:
        key = serialization.load_pem_public_key(_public_pem(public_key).encode())
        key.verify(b64decode(signature), message.encode(), padding.PKCS1v15(), hashes.SHA256())
    except Exception as exc:
        raise BizException("回调签名无效", code=BizCode.CONFLICT) from exc


def _decrypt_wechat(data: dict, api_v3_key: str) -> dict:
    resource = data.get("resource") if isinstance(data.get("resource"), dict) else None
    if resource is None:
        raise BizException("回调无效", code=BizCode.BAD_REQUEST)
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise BizException("支付未确认", code=BizCode.CONFLICT) from exc
    try:
        plain = AESGCM(api_v3_key.encode()).decrypt(
            str(resource.get("nonce") or "").encode(),
            b64decode(str(resource.get("ciphertext") or "")),
            str(resource.get("associated_data") or "").encode(),
        )
        payload = json.loads(plain.decode())
    except Exception as exc:
        raise BizException("回调签名无效", code=BizCode.CONFLICT) from exc
    if not isinstance(payload, dict):
        raise BizException("回调无效", code=BizCode.BAD_REQUEST)
    return payload


def _read_json(opener: Opener, request: Request) -> dict:
    payload = json.loads(_read_text(opener, request))
    if not isinstance(payload, dict):
        raise BizException("支付未确认", code=BizCode.CONFLICT)
    return payload


def _read_text(opener: Opener, request: Request) -> str:
    try:
        with opener(request, timeout=30) as response:
            return response.read().decode()
    except BizException:
        raise
    except Exception as exc:
        raise BizException("支付未确认", code=BizCode.CONFLICT) from exc


def _json_object(raw: str, node: str) -> str:
    marker = f'"{node}":'
    start = raw.find(marker)
    if start < 0:
        raise BizException("支付未确认", code=BizCode.CONFLICT)
    index = raw.find("{", start)
    depth = 0
    for pos in range(index, len(raw)):
        if raw[pos] == "{":
            depth += 1
        elif raw[pos] == "}":
            depth -= 1
            if depth == 0:
                return raw[index : pos + 1]
    raise BizException("支付未确认", code=BizCode.CONFLICT)


def _json_sign(raw: str) -> str:
    payload = json.loads(raw)
    sign = payload.get("sign") if isinstance(payload, dict) else None
    if not isinstance(sign, str) or not sign:
        raise BizException("回调签名无效", code=BizCode.CONFLICT)
    return sign


def _public_pem(value: str) -> str:
    text = value.strip()
    if "BEGIN" in text:
        return text
    body = "\n".join(text[index : index + 64] for index in range(0, len(text), 64))
    return f"-----BEGIN PUBLIC KEY-----\n{body}\n-----END PUBLIC KEY-----"


def _same_money(left: object, right: object) -> bool:
    try:
        return Decimal(str(left)).normalize() == Decimal(str(right)).normalize()
    except Exception:
        return False


def _text(value: object, message: str) -> str:
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        raise BizException(message, code=BizCode.BAD_REQUEST)
    return value.strip()
