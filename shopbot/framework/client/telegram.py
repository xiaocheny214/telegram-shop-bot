"""给买家发 Telegram 消息，或删掉已经发出的那一条。令牌来自应用配置，调用时才读取。"""

import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from shopbot.framework.config.app import get_app_settings
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException


def send_telegram_message(chat_id: int, text: str) -> None:
    payload = _call("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
    if payload is None or payload.get("ok") is not True:
        raise BizException("发货失败", code=BizCode.BAD_REQUEST)


def send_telegram_notice(chat_id: int, text: str, reply_markup: dict | None = None) -> bool:
    """发一条新消息。失败时返回 False，不抛给支付回调。"""
    body: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_markup is not None:
        body["reply_markup"] = reply_markup
    payload = _call("sendMessage", body)
    return payload is not None and payload.get("ok") is True


def delete_telegram_message(chat_id: int, message_id: int) -> bool:
    """删掉一条已发出的消息。消息已经不在时当作成功。"""
    payload = _call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    if payload is not None and payload.get("ok") is True:
        return True
    description = str(payload.get("description") or "") if isinstance(payload, dict) else ""
    lowered = description.lower()
    return "not found" in lowered or "message to delete not found" in lowered


def _call(method: str, body: dict) -> dict | None:
    request = Request(
        f"https://api.telegram.org/bot{get_app_settings().bot_token}/{method}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
    except HTTPError as exc:
        return _error_payload(exc)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _error_payload(exc: HTTPError) -> dict:
    try:
        payload = json.loads(exc.read().decode())
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}
