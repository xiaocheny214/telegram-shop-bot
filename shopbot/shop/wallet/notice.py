"""充值到账后，按发起端更新原来的待支付展示。

目前只有 Telegram：另发一条已到账消息，并删掉原来的待支付消息。
卡网和 App 以后各自更新自己的展示，不从这里发 Telegram。
"""

import asyncio
import html
import logging
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shopbot.framework.client.telegram import delete_telegram_message, send_telegram_notice
from shopbot.framework.enums.model import ClientPlatform, OrderStatus, UsdtNetwork
from shopbot.shop.models import RechargeClient, User, WalletRecharge

logger = logging.getLogger(__name__)

_NETWORK = {
    int(UsdtNetwork.TRC20): "TRC20",
    int(UsdtNetwork.ERC20): "ERC20",
    int(UsdtNetwork.SOLANA): "Solana",
    int(UsdtNetwork.POLYGON): "Polygon",
}


@dataclass(frozen=True)
class _TelegramNotice:
    recharge_id: int
    recharge_no: str
    chat_id: int
    message_id: int
    text: str
    reply_markup: dict


async def refresh_recharge_notice(factory: async_sessionmaker[AsyncSession], recharge_no: str) -> None:
    """已支付的充值才通知。先占住待支付消息，再发新消息并删掉旧的。失败不回滚入账。"""
    try:
        notice = await _telegram_notice(factory, recharge_no)
    except Exception:
        logger.exception("更新充值消息失败 recharge_no=%s", recharge_no)
        return
    if notice is None:
        return
    if not await _take_message(factory, notice.recharge_id, notice.message_id):
        return
    sent = False
    try:
        sent = await asyncio.to_thread(send_telegram_notice, notice.chat_id, notice.text, notice.reply_markup)
        if not sent:
            logger.warning("充值到账消息没有发出 recharge_no=%s", notice.recharge_no)
            return
        removed = await asyncio.to_thread(delete_telegram_message, notice.chat_id, notice.message_id)
        if not removed:
            logger.warning("待支付消息没有撤销 recharge_no=%s", notice.recharge_no)
    except Exception:
        logger.exception("更新充值消息失败 recharge_no=%s", recharge_no)
    finally:
        if not sent:
            await _restore_message(factory, notice.recharge_id, notice.message_id)


async def _telegram_notice(factory: async_sessionmaker[AsyncSession], recharge_no: str) -> _TelegramNotice | None:
    async with factory() as session:
        recharge = await session.scalar(select(WalletRecharge).where(WalletRecharge.recharge_no == recharge_no))
        if recharge is None or recharge.status != OrderStatus.PAID:
            return None
        client = await session.scalar(select(RechargeClient).where(RechargeClient.recharge_id == recharge.id))
        if client is None or client.platform != ClientPlatform.TELEGRAM:
            return None
        if client.chat_id is None or client.message_id is None:
            return None
        user = await session.get(User, recharge.user_id)
        language = user.language if user is not None and user.language in {"zh", "en"} else "zh"
        return _TelegramNotice(
            recharge_id=recharge.id,
            recharge_no=recharge.recharge_no,
            chat_id=client.chat_id,
            message_id=client.message_id,
            text=_paid_text(language, recharge),
            reply_markup=_menu(language == "en"),
        )


async def _take_message(factory: async_sessionmaker[AsyncSession], recharge_id: int, message_id: int) -> bool:
    """同一条待支付消息只通知一次。占住之后重复回调不再发新消息。"""
    async with factory() as session:
        async with session.begin():
            result = await session.execute(
                update(RechargeClient)
                .where(RechargeClient.recharge_id == recharge_id, RechargeClient.message_id == message_id)
                .values(message_id=None)
            )
            return result.rowcount == 1


async def _restore_message(factory: async_sessionmaker[AsyncSession], recharge_id: int, message_id: int) -> None:
    """新消息没发出去时把位置放回去，下次回调还能再试。"""
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(RechargeClient)
                .where(RechargeClient.recharge_id == recharge_id, RechargeClient.message_id.is_(None))
                .values(message_id=message_id)
            )


def _paid_text(language: str, recharge: WalletRecharge) -> str:
    english = language == "en"

    def row(label: str, value: str) -> str:
        return f"<b>{html.escape(label)}</b>  {html.escape(value)}"

    amount = f"{recharge.amount_usdt_cents // 100}.{recharge.amount_usdt_cents % 100:02d} USDT"
    network = _NETWORK.get(recharge.pay_network) if recharge.pay_network is not None else None
    if english:
        lines = [
            "<b>Top-up received</b>",
            "The balance has been credited.",
            row("No.", recharge.recharge_no),
            row("Amount", amount),
        ]
        if recharge.pay_amount:
            lines.append(row("Pay", recharge.pay_amount))
        if network:
            lines.append(row("Network", network))
        lines.append(row("Status", "Paid"))
    else:
        lines = [
            "<b>充值已到账</b>",
            "金额已记入余额。",
            row("单号", recharge.recharge_no),
            row("金额", amount),
        ]
        if recharge.pay_amount:
            lines.append(row("实付", recharge.pay_amount))
        if network:
            lines.append(row("网络", network))
        lines.append(row("状态", "已支付"))
    return "\n\n".join(lines)


def _menu(english: bool) -> dict:
    def button(zh: str, en: str, data: str) -> dict:
        return {"text": en if english else zh, "callback_data": data}

    return {
        "inline_keyboard": [
            [button("钱包", "Wallet", "m:wallet"), button("主菜单", "Main menu", "m:home")],
        ]
    }
