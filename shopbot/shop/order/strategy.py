"""下单扣库存的策略。商品上的 stock_deduct_strategy 决定用哪一个。"""

from sqlalchemy.ext.asyncio import AsyncSession

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import StockDeductStrategy
from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.card.service import release_reserved_cards, reserve_cards, sell_card, sell_reserved_cards
from shopbot.shop.models import Order


class StockStrategy:
    def quantity(self, value: int) -> int:
        return value

    async def after_create(self, session: AsyncSession, order: Order) -> None:
        return None

    async def after_paid(self, session: AsyncSession, order: Order) -> None:
        return None

    async def after_close(self, session: AsyncSession, order: Order) -> None:
        return None


class DailyStockStrategy(StockStrategy):
    """日常策略：按购买数量下单就占用，关闭时全部放回，支付成功后改为已售。"""

    async def after_create(self, session: AsyncSession, order: Order) -> None:
        await reserve_cards(session, order.id, order.product_number)

    async def after_paid(self, session: AsyncSession, order: Order) -> None:
        await sell_reserved_cards(session, order.id)

    async def after_close(self, session: AsyncSession, order: Order) -> None:
        await release_reserved_cards(session, order.id)


class MarketingStockStrategy(StockStrategy):
    """营销策略：一笔订单固定一张卡密，支付成功时才占用。"""

    def quantity(self, value: int) -> int:
        if value != 1:
            raise BizException("营销商品一次只能购买 1 件", code=BizCode.BAD_REQUEST)
        return 1

    async def after_paid(self, session: AsyncSession, order: Order) -> None:
        await sell_card(session, order.id)


def stock_strategy(value: int) -> StockStrategy:
    if value == StockDeductStrategy.DAILY:
        return DailyStockStrategy()
    if value == StockDeductStrategy.MARKETING:
        return MarketingStockStrategy()
    raise BizException("库存扣减策略无效", code=BizCode.BAD_REQUEST)


