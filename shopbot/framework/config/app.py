"""应用配置。读取环境变量时不连接数据库。"""

import os
from dataclasses import dataclass
from functools import lru_cache

from shopbot.framework.config.database import DatabaseConfig, get_database_settings
from shopbot.framework.config.env import load_env
from shopbot.framework.crypto.secrets import load_private_key_pem


@dataclass(frozen=True)
class AppSettings:
    bot_token: str
    merchant_bot_token: str
    payment_private_key: str
    admin_telegram_ids: frozenset[int]
    database: DatabaseConfig
    host: str
    port: int
    public_base_url: str


@lru_cache
def get_app_settings() -> AppSettings:
    load_env()
    raw_ids = os.environ.get("ADMIN_TELEGRAM_IDS", "")
    admin_ids = frozenset(int(item) for item in raw_ids.split(",") if item.strip())
    buyer_token = _required("BOT_TOKEN")
    merchant_token = _required("MERCHANT_BOT_TOKEN")
    if buyer_token == merchant_token:
        raise RuntimeError("买家和商家机器人必须使用不同的令牌")
    return AppSettings(
        bot_token=buyer_token,
        merchant_bot_token=merchant_token,
        payment_private_key=load_private_key_pem(),
        admin_telegram_ids=admin_ids,
        database=get_database_settings(),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        public_base_url=_public_base_url(),
    )


def _public_base_url() -> str:
    """支付回调的公网根地址。渠道配置里不再向商家询问。"""
    value = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not value:
        return ""
    from urllib.parse import urlsplit

    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("PUBLIC_BASE_URL 必须是 https")
    return value


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value
