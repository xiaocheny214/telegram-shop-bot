"""在现有事件循环里启动两个长轮询机器人。支付回调仍走 FastAPI。"""

import asyncio
import warnings
from collections import OrderedDict

from telegram.ext import Application
from telegram.warnings import PTBUserWarning

from shopbot.bot.buyer.app import register_buyer
from shopbot.bot.merchant.app import register_merchant
from shopbot.framework.config.app import get_app_settings


def build_buyer(session_factory) -> Application:
    _quiet_form_warning()
    application = _application(get_app_settings().bot_token, session_factory)
    register_buyer(application)
    return application


def build_merchant(session_factory) -> Application:
    _quiet_form_warning()
    application = _application(get_app_settings().merchant_bot_token, session_factory)
    register_merchant(application)
    return application


async def start_bots(session_factory) -> list[Application]:
    applications = [build_buyer(session_factory), build_merchant(session_factory)]
    started: list[Application] = []
    try:
        for application in applications:
            await application.initialize()
            started.append(application)
            await application.start()
            if application.updater is None:
                raise RuntimeError("机器人轮询未就绪")
            await application.updater.start_polling(drop_pending_updates=True)
    except Exception:
        await stop_bots(started)
        raise
    return started


async def stop_bots(applications: list[Application]) -> None:
    for application in reversed(applications):
        updater = application.updater
        if updater is not None and updater.running:
            await updater.stop()
        if application.running:
            await application.stop()
        await application.shutdown()


def _quiet_form_warning() -> None:
    """表单要同时收按钮和文字，不能按单条消息跟踪会话。这条提示对这种用法没有帮助。"""
    warnings.filterwarnings(
        "ignore",
        message=r"If 'per_message=False'",
        category=PTBUserWarning,
    )


_CONCURRENCY = 8
_CHAT_LOCKS: OrderedDict[tuple[int, int], asyncio.Lock] = OrderedDict()
_CHAT_LOCK_MAX = 4_096


def _application(token: str, session_factory) -> Application:
    application = (
        Application.builder()
        .token(token)
        .job_queue(None)
        .concurrent_updates(_CONCURRENCY)
        .connection_pool_size(_CONCURRENCY * 2)
        .pool_timeout(10)
        .connect_timeout(5)
        .build()
    )
    application.bot_data["session_factory"] = session_factory
    _isolate_chats(application)
    return application


def _isolate_chats(application: Application) -> None:
    """不同聊天同时处理。同一个聊天仍按顺序，避免表单状态互相覆盖。"""
    original = application.process_update

    async def process_update(update: object) -> None:
        chat = getattr(update, "effective_chat", None)
        if chat is None:
            await original(update)
            return
        async with _lock_for(id(application), chat.id):
            await original(update)

    application.process_update = process_update  # type: ignore[method-assign]


def _lock_for(bot_id: int, chat_id: int) -> asyncio.Lock:
    key = (bot_id, chat_id)
    lock = _CHAT_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _CHAT_LOCKS[key] = lock
    _CHAT_LOCKS.move_to_end(key)
    while len(_CHAT_LOCKS) > _CHAT_LOCK_MAX:
        _CHAT_LOCKS.popitem(last=False)
    return lock
