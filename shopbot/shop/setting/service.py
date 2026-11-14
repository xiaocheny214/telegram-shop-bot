"""读取和修改店铺设置。

没有记录时写入初始超时 900 秒、待支付上限 3。
汇率为 0 表示未配置。写入在事务中执行。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from shopbot.framework.db import transactional
from shopbot.shop.models import ShopSetting
from shopbot.shop.setting.schemas import ShopSettingUpdate, parse_model
from shopbot.system.audit.service import audit

_DEFAULT_TIMEOUT_SECONDS = 900
_DEFAULT_PENDING_LIMIT = 3


@transactional
async def get_shop_setting(session: AsyncSession) -> ShopSetting:
    setting = await session.get(ShopSetting, 1)
    if setting is not None:
        return setting
    setting = ShopSetting(
        id=1,
        order_timeout_seconds=_DEFAULT_TIMEOUT_SECONDS,
        max_pending_orders=_DEFAULT_PENDING_LIMIT,
    )
    session.add(setting)
    await session.flush()
    return setting


@audit("shop_setting.update")
@transactional
async def update_shop_setting(session: AsyncSession, payload: ShopSettingUpdate | dict) -> ShopSetting:
    data = parse_model(ShopSettingUpdate, payload)
    setting = await get_shop_setting(session)
    if "order_timeout_seconds" in data.model_fields_set and data.order_timeout_seconds is not None:
        setting.order_timeout_seconds = data.order_timeout_seconds
    if "max_pending_orders" in data.model_fields_set and data.max_pending_orders is not None:
        setting.max_pending_orders = data.max_pending_orders
    if "thank_you_message" in data.model_fields_set:
        setting.thank_you_message = data.thank_you_message
    if "usd_cny_cents" in data.model_fields_set and data.usd_cny_cents is not None:
        setting.usd_cny_cents = data.usd_cny_cents
    if "usdt_cny_cents" in data.model_fields_set and data.usdt_cny_cents is not None:
        setting.usdt_cny_cents = data.usdt_cny_cents
    if "gbp_cny_cents" in data.model_fields_set and data.gbp_cny_cents is not None:
        setting.gbp_cny_cents = data.gbp_cny_cents
    if "eur_cny_cents" in data.model_fields_set and data.eur_cny_cents is not None:
        setting.eur_cny_cents = data.eur_cny_cents
    if "display_name" in data.model_fields_set:
        setting.display_name = data.display_name
    await session.flush()
    return setting
