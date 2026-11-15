"""机器人界面语言。中文是原文，英文在选择后替换。商品名称和卡密不翻译。"""

from contextvars import ContextVar

_language: ContextVar[str] = ContextVar("bot_language", default="zh")

_ERRORS = {
    "参数无效": "Invalid parameter",
    "暂时无法处理，请稍后再试": "Please try again later",
    "用户已停用": "This user is disabled",
    "无权访问": "Access denied",
    "用户不存在": "User not found",
    "金额无效": "Invalid amount",
    "金额超出范围": "Amount is out of range",
    "购买数量无效": "Invalid quantity",
    "库存不足": "Out of stock",
    "余额不足": "Insufficient balance",
    "余额超出上限": "Balance limit reached",
    "商品已下架": "This product is unavailable",
    "商品不存在": "Product not found",
    "规格不存在": "Option not found",
    "规格已下架": "This option is unavailable",
    "该商品有规格，请选择规格": "Choose an option for this product",
    "该商品没有规格": "This product has no options",
    "支付渠道未启用": "This payment channel is disabled",
    "支付渠道不可用。": "This payment channel is unavailable.",
    "订单不存在": "Order not found",
    "订单不在待支付": "This order is not awaiting payment",
    "请关闭订单": "Close an open order first",
    "请关闭充值": "Close an open top-up first",
    "请稍后再试": "Please try again later",
    "未配置USDT汇率": "USDT rate is not configured",
    "未配置汇率": "Exchange rate is not configured",
    "折算后金额过小": "Converted amount is too small",
    "用户不可用": "This user is unavailable",
    "支付渠道不符": "Payment channel does not match",
    "支付金额不符": "Payment amount does not match",
    "订单号生成失败": "Could not create an order number",
    "充值单不在待支付": "This top-up is not awaiting payment",
    "营销商品一次只能购买 1 件": "This product can only be bought one at a time",
    "文件编码必须是 UTF-8": "The file must be UTF-8",
    "卡密内容不能为空": "Card content cannot be empty",
    "只支持 .txt 文件": "Only .txt files are supported",
    "文件不能超过 1MB": "The file must be 1MB or smaller",
    "没有可导入的卡密": "No cards to import",
    "导入后库存超出范围": "Stock would exceed the limit",
    "一次最多导入 500 条卡密": "Import at most 500 cards at a time",
    "分类不存在": "Category not found",
    "分类下还有商品，不能删除": "This category still has products",
    "分类名称已存在": "A category with this name already exists",
    "同分类下商品名称已存在": "A product with this name already exists in the category",
    "商品有未发货的手动订单，不能删除": "This product has an unshipped manual order and cannot be deleted",
    "商品有待支付的自动发货订单，不能删除": "This product has an unpaid automatic order and cannot be deleted",
    "商品还有卡密，不能删除": "This product still has cards and cannot be deleted",
    "规格名称已存在": "An option with this name already exists",
    "规格已有订单，不能删除": "This option has orders and cannot be deleted",
    "规格还有卡密，不能删除": "This option still has cards and cannot be deleted",
    "支付渠道不存在": "Payment channel not found",
    "支付渠道无效": "Invalid payment channel",
    "支付渠道配置不存在": "Payment channel settings are missing",
    "支付渠道下单失败": "The payment channel rejected the order",
    "未配置公网地址": "The public site URL is not configured",
    "请选择 USDT 网络": "Choose a USDT network",
    "该渠道不支持此货币": "This channel does not support this currency",
    "请选择易支付的支付宝或微信": "Choose Alipay or WeChat for Epay",
    "内容不能为空。": "Content cannot be empty.",
    "动作名无效。": "Invalid action name.",
    "排序超出范围": "Sort order is out of range",
    "订单超时必须在 60 到 86400 秒之间": "Timeout must be between 60 and 86400 seconds",
    "待支付上限必须在 1 到 100 之间": "The pending-order limit must be between 1 and 100",
    "语言无效": "Invalid language",
    "发货失败": "Delivery failed",
    "没有可发货的卡密": "No card is ready to deliver",
    "订单已发货": "This order was already delivered",
    "订单不在待发货": "This order is not waiting for delivery",
    "消息过长": "The message is too long",
    "请稍后再试": "Please try again later",
    "店名必须是文本": "Shop name must be text",
    "店名不能超过 64 个字符": "Shop name must be 64 characters or fewer",
    "店名包含非法字符": "Shop name contains an invalid character",
    "分类图标不能超过 16 个字符": "Category icon must be 16 characters or fewer",
    "分类图标包含非法字符": "Category icon contains an invalid character",
}


def use_language(language: str | None) -> None:
    _language.set(language if language in {"zh", "en"} else "zh")


def current_language() -> str:
    return _language.get()


def say(zh: str, en: str, /, **kwargs: object) -> str:
    """按当前语言取句子。有参数时用 format 填入。"""
    text = en if _language.get() == "en" else zh
    return text.format(**kwargs) if kwargs else text


def tr(message: str) -> str:
    """业务异常的中文原文在英文界面下替换。对不上的句子保持原样。"""
    if _language.get() != "en":
        return message
    found = _ERRORS.get(message)
    if found is not None:
        return found
    if message.startswith("未配置") and message.endswith("汇率"):
        name = message.removeprefix("未配置").removesuffix("汇率")
        labels = {"美元": "USD", "英镑": "GBP", "欧元": "EUR", "人民币": "CNY"}
        return f"{labels.get(name, name)} rate is not configured"
    if message.startswith("第 ") and "行：" in message:
        return "Card import: " + message
    return message
