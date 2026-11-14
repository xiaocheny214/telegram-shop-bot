"""给买家发 Telegram 消息。令牌来自应用配置，调用时才读取。"""

import json
from urllib.request import Request, urlopen

from shopbot.framework.config.app import get_app_settings
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException


def send_telegram_message(chat_id: int, text: str) -> None:
    token = get_app_settings().bot_token
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(
            {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
    except Exception:
        raise BizException("发货失败", code=BizCode.BAD_REQUEST)
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise BizException("发货失败", code=BizCode.BAD_REQUEST)
