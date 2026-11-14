"""系统表。支付渠道配置不放在商店包里。

列表只放供应商、排序和启用状态。密钥和地址按供应商拆到明细表，打开配置时才读。
定时任务也放这里。间隔和启停由管理员配置，启动器只负责到点调用。
高危操作的审计记录也放这里，不保存卡密、密钥和回调报文。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from shopbot.framework.db import Base
from shopbot.framework.enums.model import PaymentProvider


class PaymentChannel(Base):
    """渠道列表。"""

    __tablename__ = "payment_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[int] = mapped_column(default=PaymentProvider.ALIPAY, index=True)  # 见 PaymentProvider
    epay_method: Mapped[int | None] = mapped_column()  # 仅易支付。见 EpayMethod。列表要区分支付宝和微信
    sort_order: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("1"))


class AlipayChannel(Base):
    """支付宝原始渠道的配置。"""

    __tablename__ = "payment_alipay"

    channel_id: Mapped[int] = mapped_column(ForeignKey("payment_channels.id"), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(64))  # 应用 appId
    private_key: Mapped[str] = mapped_column(Text)  # 应用私钥。公钥加密后存放
    alipay_public_key: Mapped[str] = mapped_column(Text)  # 支付宝公钥。公钥加密后存放
    api_url: Mapped[str] = mapped_column(String(512))  # 网关地址
    notify_url: Mapped[str] = mapped_column(Text)  # 异步回调地址


class WechatChannel(Base):
    """微信原始渠道的配置。"""

    __tablename__ = "payment_wechat"

    channel_id: Mapped[int] = mapped_column(ForeignKey("payment_channels.id"), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(64))  # APPID
    merchant_id: Mapped[str] = mapped_column(String(64))  # 商户号
    api_v3_key: Mapped[str] = mapped_column(Text)  # APIv3 密钥。公钥加密后存放
    cert_serial: Mapped[str] = mapped_column(String(128))  # 证书序列号
    private_key_path: Mapped[str] = mapped_column(Text)  # 私钥文件路径。公钥加密后存放
    notify_url: Mapped[str] = mapped_column(Text)  # 异步回调地址


class EpayChannel(Base):
    """易支付配置。支付宝和微信共用这张表，方式记在渠道列表上。"""

    __tablename__ = "payment_epay"

    channel_id: Mapped[int] = mapped_column(ForeignKey("payment_channels.id"), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(String(64))  # 商户 ID
    merchant_key: Mapped[str] = mapped_column(Text)  # 商户密钥。公钥加密后存放
    api_url: Mapped[str] = mapped_column(String(512))  # api 地址
    notify_url: Mapped[str] = mapped_column(Text)  # 异步回调地址
    return_url: Mapped[str] = mapped_column(Text)  # 同步跳转地址


class BepusdtChannel(Base):
    """BEpusdt 配置。USDT 网络在下单时选择，不写在这里。"""

    __tablename__ = "payment_bepusdt"

    channel_id: Mapped[int] = mapped_column(ForeignKey("payment_channels.id"), primary_key=True)
    api_url: Mapped[str] = mapped_column(String(512))  # 网关地址
    api_token: Mapped[str] = mapped_column(Text)  # 网关令牌。公钥加密后存放
    notify_url: Mapped[str] = mapped_column(Text)  # 异步回调地址


class ScheduledTask(Base):
    """管理员可改的定时任务。业务函数由启动器按 code 调用。"""

    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)  # 启动器注册名
    name: Mapped[str] = mapped_column(String(128))  # 给管理员看的名称
    interval_seconds: Mapped[int] = mapped_column(default=5)  # 两次执行的间隔秒数
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("1"))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(255))  # 最近一次失败原因，不含业务数据


class AuditLog(Base):
    """高危操作审计。操作者编号不建外键，用户删除后记录仍保留。"""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_operator_telegram_id", "operator_telegram_id"),
        Index("ix_audit_logs_action", "action"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    operator_user_id: Mapped[int | None] = mapped_column()  # users.id。未登录为空
    operator_telegram_id: Mapped[int | None] = mapped_column(BigInteger)  # 未登录为空
    action: Mapped[str] = mapped_column(String(128))  # 稳定动作名。权限拒绝时用请求路径
    path: Mapped[str | None] = mapped_column(String(255))  # HTTP 路径。非 HTTP 调用为空
    status_code: Mapped[int]  # 与接口 HTTP 状态一致。成功 200
    result: Mapped[str] = mapped_column(String(255))  # success 或失败原因，不含业务机密
    duration_ms: Mapped[int]
    client_ip: Mapped[str | None] = mapped_column(String(64))
