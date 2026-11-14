"""角色注解。判断在 service，HTTP 和 Telegram 的挂载在 guard。"""

from shopbot.system.access.guard import ACCESS_ROLES, current_telegram_id, enforce_access, require
from shopbot.system.access.service import ensure_access

__all__ = [
    "ACCESS_ROLES",
    "current_telegram_id",
    "enforce_access",
    "ensure_access",
    "require",
]
