"""Telegram 入口。买家和管理员操作调用 shop 包里对应的 service。"""

from shopbot.bot.runtime import start_bots, stop_bots

__all__ = ["start_bots", "stop_bots"]
