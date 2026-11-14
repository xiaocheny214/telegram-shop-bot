"""新增、查看和修改支付渠道。

渠道列表只保存供应商、排序和启用状态。配置明细按支付宝、微信、易支付、BEpusdt 分开存放。
写入在事务中执行。FastAPI 已开启事务时并入该事务。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shopbot.framework.db import transactional
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import PaymentProvider
from shopbot.framework.exceptions.biz import BizException
from shopbot.system.models import AlipayChannel, BepusdtChannel, EpayChannel, PaymentChannel, WechatChannel
from shopbot.system.audit.service import audit
from shopbot.system.payment.notify import payment_notify_url
from shopbot.system.payment.schemas import PaymentChannelCreate, parse_channel_id, parse_model
from shopbot.system.vault import protect, reveal

ChannelDetail = AlipayChannel | WechatChannel | EpayChannel | BepusdtChannel

_DETAIL_MODELS = {
    PaymentProvider.ALIPAY: AlipayChannel,
    PaymentProvider.WECHAT: WechatChannel,
    PaymentProvider.EPAY: EpayChannel,
    PaymentProvider.BEPUSDT: BepusdtChannel,
}


@audit("payment_channel.create")
@transactional
async def create_payment_channel(
    session: AsyncSession,
    payload: PaymentChannelCreate | dict,
) -> tuple[PaymentChannel, ChannelDetail]:
    data = parse_model(PaymentChannelCreate, payload)
    channel = PaymentChannel(
        provider=data.provider,
        epay_method=data.epay_method if data.provider == PaymentProvider.EPAY else None,
        sort_order=data.sort_order,
        is_active=data.is_active,
    )
    session.add(channel)
    await session.flush()
    detail = _new_detail(channel.id, data)
    session.add(detail)
    await session.flush()
    return channel, _reveal(detail)


async def list_payment_channels(session: AsyncSession) -> list[PaymentChannel]:
    result = await session.scalars(
        select(PaymentChannel).order_by(PaymentChannel.sort_order, PaymentChannel.id).limit(100)
    )
    return list(result.all())


async def get_payment_channel(session: AsyncSession, channel_id: int) -> tuple[PaymentChannel, ChannelDetail]:
    """返回解密后的副本。会话里的原行保持密文，避免随后的提交把明文写回去。"""
    channel, detail = await _stored_channel(session, channel_id)
    return channel, _reveal(detail)


@audit("payment_channel.update")
@transactional
async def update_payment_channel(
    session: AsyncSession,
    channel_id: int,
    payload: PaymentChannelCreate | dict,
) -> tuple[PaymentChannel, ChannelDetail]:
    channel, detail = await _stored_channel(session, channel_id)
    data = parse_model(PaymentChannelCreate, payload)
    provider_changed = channel.provider != data.provider
    channel.provider = data.provider
    channel.epay_method = data.epay_method if data.provider == PaymentProvider.EPAY else None
    channel.sort_order = data.sort_order
    channel.is_active = data.is_active
    if provider_changed:
        await session.delete(detail)
        await session.flush()
        detail = _new_detail(channel.id, data)
        session.add(detail)
    else:
        _apply_detail(detail, data)
    await session.flush()
    return channel, _reveal(detail)


async def _stored_channel(session: AsyncSession, channel_id: int) -> tuple[PaymentChannel, ChannelDetail]:
    channel = await session.get(PaymentChannel, parse_channel_id(channel_id))
    if channel is None:
        raise BizException("支付渠道不存在", code=BizCode.NOT_FOUND)
    detail = await _load_detail(session, channel)
    return channel, detail


async def _load_detail(session: AsyncSession, channel: PaymentChannel) -> ChannelDetail:
    model = _DETAIL_MODELS.get(PaymentProvider(channel.provider))
    if model is None:
        raise BizException("支付渠道无效", code=BizCode.BAD_REQUEST)
    detail = await session.get(model, channel.id)
    if detail is None:
        raise BizException("支付渠道配置不存在", code=BizCode.NOT_FOUND)
    return detail


def _new_detail(channel_id: int, data: PaymentChannelCreate) -> ChannelDetail:
    provider = PaymentProvider(data.provider)
    if provider is PaymentProvider.ALIPAY:
        detail: ChannelDetail = AlipayChannel(channel_id=channel_id, app_id="", private_key="", alipay_public_key="", api_url="", notify_url="")
    elif provider is PaymentProvider.WECHAT:
        detail = WechatChannel(
            channel_id=channel_id,
            app_id="",
            merchant_id="",
            api_v3_key="",
            cert_serial="",
            private_key_path="",
            notify_url="",
        )
    elif provider is PaymentProvider.EPAY:
        detail = EpayChannel(channel_id=channel_id, merchant_id="", merchant_key="", api_url="", notify_url="", return_url="")
    else:
        detail = BepusdtChannel(channel_id=channel_id, api_url="", api_token="", notify_url="")
    return _apply_detail(detail, data)


def _apply_detail(detail: ChannelDetail, data: PaymentChannelCreate) -> ChannelDetail:
    if isinstance(detail, AlipayChannel):
        detail.app_id = data.app_id or ""
        detail.private_key = protect(data.private_key or "") or ""
        detail.alipay_public_key = protect(data.alipay_public_key or "") or ""
        detail.api_url = data.api_url or ""
        detail.notify_url = data.notify_url
    elif isinstance(detail, WechatChannel):
        detail.app_id = data.app_id or ""
        detail.merchant_id = data.merchant_id or ""
        detail.api_v3_key = protect(data.api_v3_key or "") or ""
        detail.cert_serial = data.cert_serial or ""
        detail.private_key_path = protect(data.private_key_path or "") or ""
        detail.notify_url = data.notify_url
    elif isinstance(detail, EpayChannel):
        detail.merchant_id = data.merchant_id or ""
        detail.merchant_key = protect(data.merchant_key or "") or ""
        detail.api_url = data.api_url or ""
        detail.notify_url = data.notify_url
        detail.return_url = data.return_url or ""
    else:
        detail.api_url = data.api_url or ""
        detail.api_token = protect(data.api_token or "") or ""
        detail.notify_url = data.notify_url or payment_notify_url(detail.channel_id)
    return detail


def _reveal(detail: ChannelDetail) -> ChannelDetail:
    """复制一份再解密。原对象仍挂在会话上，字段保持密文。"""
    if isinstance(detail, AlipayChannel):
        return AlipayChannel(
            channel_id=detail.channel_id,
            app_id=detail.app_id,
            private_key=reveal(detail.private_key) or "",
            alipay_public_key=reveal(detail.alipay_public_key) or "",
            api_url=detail.api_url,
            notify_url=detail.notify_url,
        )
    if isinstance(detail, WechatChannel):
        return WechatChannel(
            channel_id=detail.channel_id,
            app_id=detail.app_id,
            merchant_id=detail.merchant_id,
            api_v3_key=reveal(detail.api_v3_key) or "",
            cert_serial=detail.cert_serial,
            private_key_path=reveal(detail.private_key_path) or "",
            notify_url=detail.notify_url,
        )
    if isinstance(detail, EpayChannel):
        return EpayChannel(
            channel_id=detail.channel_id,
            merchant_id=detail.merchant_id,
            merchant_key=reveal(detail.merchant_key) or "",
            api_url=detail.api_url,
            notify_url=detail.notify_url,
            return_url=detail.return_url,
        )
    return BepusdtChannel(
        channel_id=detail.channel_id,
        api_url=detail.api_url,
        api_token=reveal(detail.api_token) or "",
        notify_url=detail.notify_url,
    )
