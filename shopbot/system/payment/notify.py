"""支付回调地址。由本服务的公网根地址和渠道编号拼出，不由商家填写。"""

from shopbot.framework.config.app import get_app_settings


def payment_notify_url(channel_id: int) -> str:
    base = get_app_settings().public_base_url
    if not base:
        return ""
    return f"{base}/payments/notify/{channel_id}"
