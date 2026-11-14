"""表结构。商品价格按货币的分存储，100 = 1 个货币单位。"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from shopbot.framework.db import Base
from shopbot.framework.enums.model import (
    CardStatus,
    Currency,
    DeliveryMethod,
    DeliveryStatus,
    OrderStatus,
    PaymentProvider,
    StockDeductStrategy,
    UserRole,
)


class ShopSetting(Base):
    """管理员可改的店铺设置。"""

    __tablename__ = "shop_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_timeout_seconds: Mapped[int] = mapped_column(default=900)  # 待支付订单有效秒数，初始 900，由管理员修改
    max_pending_orders: Mapped[int] = mapped_column(default=3)  # 同一用户同时存在的待支付订单上限，初始 3，由管理员修改
    thank_you_message: Mapped[str | None] = mapped_column(Text)  # 发货消息末尾的感谢话语，由管理员修改，可空
    usd_cny_cents: Mapped[int] = mapped_column(default=0, server_default=text("0"))  # 1 美元兑换的人民币分。725 表示 7.25 元，0 表示未配置
    usdt_cny_cents: Mapped[int] = mapped_column(default=0, server_default=text("0"))  # 1 USDT 兑换的人民币分。0 表示未配置
    gbp_cny_cents: Mapped[int] = mapped_column(default=0, server_default=text("0"))  # 1 英镑兑换的人民币分。0 表示未配置
    eur_cny_cents: Mapped[int] = mapped_column(default=0, server_default=text("0"))  # 1 欧元兑换的人民币分。0 表示未配置


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("balance_usdt_cents >= 0", name="ck_users_balance_non_negative"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[int] = mapped_column(default=UserRole.USER, server_default=text("1"))  # 0 管理员，1 普通用户
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("1"))
    language: Mapped[str] = mapped_column(String(8), default="zh", server_default=text("'zh'"))  # zh 中文，en 英文
    balance_usdt_cents: Mapped[int] = mapped_column(default=0)  # USDT 分。只通过钱包流水增减
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WalletLedger(Base):
    """余额流水。余额本身在 users.balance_usdt_cents。"""

    __tablename__ = "wallet_ledgers"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_usdt_cents: Mapped[int]
    direction: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(64))
    ref_type: Mapped[str | None] = mapped_column(String(32))
    ref_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WalletRecharge(Base):
    """充值。支付成功后把 USDT 分记入余额，不发货。"""

    __tablename__ = "wallet_recharges"
    __table_args__ = (Index("ix_wallet_recharges_status_expires", "status", "expires_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    recharge_no: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("payment_channels.id"))
    pay_provider: Mapped[int]
    pay_network: Mapped[int | None] = mapped_column()
    amount_usdt_cents: Mapped[int]  # 到账的 USDT 分
    status: Mapped[str] = mapped_column(String(32), default=OrderStatus.PENDING)
    channel_trade_no: Mapped[str | None] = mapped_column(String(128))
    pay_amount: Mapped[str | None] = mapped_column(String(64))  # 渠道要求实付的金额原文
    pay_target: Mapped[str | None] = mapped_column(Text)  # 收款标识。公钥加密后存放
    pay_url: Mapped[str | None] = mapped_column(Text)  # 收银台链接。公钥加密后存放
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True)


class Product(Base):
    """列表和交易会频繁读写的字段。"""

    __tablename__ = "products"
    __table_args__ = (CheckConstraint("stock >= 0", name="ck_products_stock_non_negative"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    cover_url: Mapped[str | None] = mapped_column(String(512))
    currency: Mapped[int] = mapped_column(default=Currency.USDT, server_default=text("2"))  # 见 Currency
    price_cents: Mapped[int]  # 分。100 = 1 个货币单位
    delivery_method: Mapped[int] = mapped_column(default=DeliveryMethod.AUTO, server_default=text("0"))  # 0 自动发货，1 手动发货
    stock_deduct_strategy: Mapped[int] = mapped_column(
        default=StockDeductStrategy.DAILY,
        server_default=text("0"),
    )  # 0 日常策略，下单扣库存；1 营销策略，支付成功后扣库存
    stock: Mapped[int] = mapped_column(default=0)  # 列表展示用的总库存
    sold_count: Mapped[int] = mapped_column(default=0)
    sort_order: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True)


class ProductDetail(Base):
    """详情页才读的大字段。"""

    __tablename__ = "product_details"

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    description: Mapped[str] = mapped_column(Text, default="")
    internal_remark: Mapped[str | None] = mapped_column(Text)  # 内部备注。公钥加密后存放，不展示给买家


class ProductSpec(Base):
    """规格各自计价、各自计数。货币跟随商品。"""

    __tablename__ = "product_specs"
    __table_args__ = (CheckConstraint("stock >= 0", name="ck_product_specs_stock_non_negative"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    price_cents: Mapped[int]  # 分。100 = 1 个商品货币
    stock: Mapped[int] = mapped_column(default=0)
    sold_count: Mapped[int] = mapped_column(default=0)
    sort_order: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True)


class CardStock(Base):
    """卡密数量。导入、预留、售出、作废时更新。"""

    __tablename__ = "card_stocks"
    __table_args__ = (
        UniqueConstraint("product_id", "spec_id"),
        CheckConstraint(
            "total_count >= 0 AND sold_count >= 0 AND available_count >= 0 "
            "AND reserved_count >= 0 AND discarded_count >= 0",
            name="ck_card_stocks_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    product_name: Mapped[str] = mapped_column(String(128))  # 冗余商品名称，避免再查商品表
    spec_id: Mapped[int | None] = mapped_column(ForeignKey("product_specs.id"), index=True)
    total_count: Mapped[int] = mapped_column(default=0)  # 总卡密数量
    sold_count: Mapped[int] = mapped_column(default=0)  # 已售出数量
    available_count: Mapped[int] = mapped_column(default=0)  # 可用数量
    reserved_count: Mapped[int] = mapped_column(default=0)  # 下单后占用、尚未售出的数量
    discarded_count: Mapped[int] = mapped_column(default=0)  # 已作废数量


class CardImportBatch(Base):
    """一次导入的审计记录。导入结束时写入，之后不随交易修改。"""

    __tablename__ = "card_import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)  # 批次 ID
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    product_name: Mapped[str] = mapped_column(String(128))  # 导入时的商品名称
    total_count: Mapped[int] = mapped_column(default=0)  # 导入数量
    success_count: Mapped[int] = mapped_column(default=0)  # 成功数量
    failure_count: Mapped[int] = mapped_column(default=0)  # 失败数量
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Card(Base):
    """单张卡密。导入时写入内容，占用、售出或作废时再改状态。"""

    __tablename__ = "cards"
    __table_args__ = (
        UniqueConstraint("product_id", "secret"),
        Index("ix_cards_product_spec_status", "product_id", "spec_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("card_import_batches.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    product_name: Mapped[str] = mapped_column(String(128))  # 导入时的商品名称，避免再查商品表
    spec_id: Mapped[int | None] = mapped_column(ForeignKey("product_specs.id"), index=True)
    secret: Mapped[str] = mapped_column(Text)  # 卡密内容。同一原文加密后相同，用来去重
    status: Mapped[str] = mapped_column(String(16), default=CardStatus.AVAILABLE, index=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("orders.id"), index=True)  # 占用或售出时写入，释放后清空
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Order(Base):
    """订单列表和状态流转会频繁读写。"""

    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_user_pending", "user_id", "status", "expires_at"),
        Index("ix_orders_status_expires", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)  # 雪花算法生成，不使用数据库自增
    order_no: Mapped[str] = mapped_column(String(64), unique=True)  # 与主键相同的十进制订单号，交给支付渠道
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))  # 订单归属
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    spec_id: Mapped[int | None] = mapped_column(ForeignKey("product_specs.id"))
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("payment_channels.id"))  # 渠道下单时写入。钱包扣款为空
    product_name: Mapped[str] = mapped_column(String(128))  # 下单时的商品名称
    product_number: Mapped[int] = mapped_column(default=1, server_default=text("1"))  # 购买数量
    stock_deduct_strategy: Mapped[int] = mapped_column(default=StockDeductStrategy.DAILY, server_default=text("0"))  # 下单时的库存策略，之后不随商品修改变化
    pay_provider: Mapped[int] = mapped_column(default=PaymentProvider.BEPUSDT, server_default=text("0"))  # 见 PaymentProvider
    pay_network: Mapped[int | None] = mapped_column()  # 见 UsdtNetwork。只有 BEpusdt 下单时写入，其他渠道为空
    currency: Mapped[int] = mapped_column(default=Currency.USDT, server_default=text("2"))  # 见 Currency
    amount_cents: Mapped[int]  # 下单时的价格快照，单位为分
    status: Mapped[str] = mapped_column(String(32), default=OrderStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 创建时按店铺的超时秒数写入


class OrderDetail(Base):
    """下单时写入支付详情，发货时写入卡密。列表不读。"""

    __tablename__ = "order_details"

    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id"), primary_key=True)
    secret: Mapped[str | None] = mapped_column(Text)  # 发给买家的卡密。公钥加密后存放
    channel_trade_no: Mapped[str | None] = mapped_column(String(128))  # 支付渠道交易号。BEpusdt 的 trade_id 放这里
    pay_amount: Mapped[str | None] = mapped_column(String(64))  # 渠道要求实付的金额原文。对账要用，保持明文
    pay_target: Mapped[str | None] = mapped_column(Text)  # 收款标识。公钥加密后存放
    pay_url: Mapped[str | None] = mapped_column(Text)  # 收银台或跳转链接。公钥加密后存放


class PaymentNotice(Base):
    __tablename__ = "payment_notices"
    __table_args__ = (UniqueConstraint("order_no", "channel_trade_no", "channel_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), index=True)
    channel_trade_no: Mapped[str] = mapped_column(String(128), default="")  # 与订单详情里的渠道交易号对应
    channel_status: Mapped[int]  # 渠道回调状态，含义由支付方式决定
    payload: Mapped[str] = mapped_column(Text)  # 回调原文。公钥加密后存放
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Delivery(Base):
    """发货审计。一个订单一行，记录是否送达，不保存消息正文。"""

    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id"), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=DeliveryStatus.PENDING)  # pending 待发送，sent 已送达
    attempts: Mapped[int] = mapped_column(default=0)  # 发送失败后的重试次数
    last_error: Mapped[str | None] = mapped_column(String(255))  # 最近一次失败原因，不写卡密
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 实际送达时间
