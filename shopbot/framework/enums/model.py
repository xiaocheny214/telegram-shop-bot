"""领域状态。网关状态用 bepusdt.OrderStatus，不在这里复制。"""

from enum import IntEnum, StrEnum


class UserRole(IntEnum):
    ADMIN = 0  # 管理员
    USER = 1  # 普通用户


class DeliveryMethod(IntEnum):
    AUTO = 0  # 自动发货
    MANUAL = 1  # 手动发货


class StockDeductStrategy(IntEnum):
    DAILY = 0  # 日常策略：下单就扣库存
    MARKETING = 1  # 营销策略：支付成功后才扣库存


class PaymentProvider(IntEnum):
    BEPUSDT = 0  # 加密货币网关。当前接 USDT
    STRIPE = 1  # Stripe
    EPAY = 2  # 易支付
    ALIPAY = 3  # 支付宝
    WECHAT = 4  # 微信支付
    WALLET = 5  # 钱包余额。不经过外部渠道


class EpayMethod(IntEnum):
    ALIPAY = 0  # 易支付里的支付宝
    WECHAT = 1  # 易支付里的微信


class UsdtNetwork(IntEnum):
    TRC20 = 0  # Tron。BEpusdt 请求值 usdt.trc20
    ERC20 = 1  # Ethereum。BEpusdt 请求值 usdt.erc20
    SOLANA = 2  # Solana。BEpusdt 请求值 usdt.solana
    POLYGON = 3  # Polygon。BEpusdt 请求值 usdt.polygon


class Currency(IntEnum):
    CNY = 0  # 人民币
    USD = 1  # 美元
    USDT = 2  # USDT
    GBP = 3  # 英镑
    EUR = 4  # 欧元


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    DELIVERED = "delivered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FULFILL_FAILED = "fulfill_failed"


class CardStatus(StrEnum):
    AVAILABLE = "available"  # 可用
    RESERVED = "reserved"  # 已预留
    SOLD = "sold"  # 已售出
    DISCARDED = "discarded"  # 已作废


class WalletDirection(StrEnum):
    CREDIT = "credit"
    DEBIT = "debit"


class DeliveryStatus(StrEnum):
    PENDING = "pending"  # 待发送
    SENT = "sent"  # 已送达
