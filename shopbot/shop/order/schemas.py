"""下单入参。格式由 Pydantic 校验，不负责数据库写入。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import UsdtNetwork
from shopbot.framework.exceptions.biz import BizException

_QUANTITY_MAX = 10_000


class OrderCreate(BaseModel):
    model_config = ConfigDict(strict=True)

    user_id: int
    product_id: int
    channel_id: int
    spec_id: int | None = None
    quantity: int = 1
    network: int | None = None

    @field_validator("user_id")
    @classmethod
    def check_user_id(cls, value: int) -> int:
        return _positive_id(value, "用户编号无效")

    @field_validator("product_id")
    @classmethod
    def check_product_id(cls, value: int) -> int:
        return _positive_id(value, "商品编号无效")

    @field_validator("channel_id")
    @classmethod
    def check_channel_id(cls, value: int) -> int:
        return _positive_id(value, "支付渠道编号无效")

    @field_validator("spec_id")
    @classmethod
    def check_spec_id(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _positive_id(value, "规格编号无效")

    @field_validator("quantity")
    @classmethod
    def check_quantity(cls, value: int) -> int:
        if type(value) is not int or not 1 <= value <= _QUANTITY_MAX:
            raise ValueError("购买数量无效")
        return value

    @field_validator("network")
    @classmethod
    def check_network(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if type(value) is not int or value not in UsdtNetwork._value2member_map_:
            raise ValueError("USDT 网络无效")
        return value


class WalletPayCreate(BaseModel):
    """用钱包余额购买商品。不走外部支付渠道。"""

    model_config = ConfigDict(strict=True)

    user_id: int
    product_id: int
    spec_id: int | None = None
    quantity: int = 1

    @field_validator("user_id")
    @classmethod
    def check_wallet_user_id(cls, value: int) -> int:
        return _positive_id(value, "用户编号无效")

    @field_validator("product_id")
    @classmethod
    def check_wallet_product_id(cls, value: int) -> int:
        return _positive_id(value, "商品编号无效")

    @field_validator("spec_id")
    @classmethod
    def check_wallet_spec_id(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _positive_id(value, "规格编号无效")

    @field_validator("quantity")
    @classmethod
    def check_wallet_quantity(cls, value: int) -> int:
        if type(value) is not int or not 1 <= value <= _QUANTITY_MAX:
            raise ValueError("购买数量无效")
        return value


_PAGE_SIZE_MAX = 20


class UserOrderQuery(BaseModel):
    """某个买家的订单分页。"""

    model_config = ConfigDict(strict=True)

    user_id: int
    page: int = 1
    page_size: int = 5

    @field_validator("user_id")
    @classmethod
    def check_page_user_id(cls, value: int) -> int:
        return _positive_id(value, "用户编号无效")

    @field_validator("page")
    @classmethod
    def check_page(cls, value: int) -> int:
        return _page(value)

    @field_validator("page_size")
    @classmethod
    def check_page_size(cls, value: int) -> int:
        return _page_size(value)


class UserOrderGet(BaseModel):
    """买家查看或取消自己的一笔订单。"""

    model_config = ConfigDict(strict=True)

    user_id: int
    order_id: int

    @field_validator("user_id")
    @classmethod
    def check_owner_id(cls, value: int) -> int:
        return _positive_id(value, "用户编号无效")

    @field_validator("order_id")
    @classmethod
    def check_order_id(cls, value: int) -> int:
        return _positive_id(value, "订单编号无效")


def parse_model[ModelT: BaseModel](model: type[ModelT], value: ModelT | dict[str, Any]) -> ModelT:
    if isinstance(value, model):
        return value
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise BizException(_message(exc), code=BizCode.BAD_REQUEST) from exc


def parse_order_id(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise BizException("订单编号无效", code=BizCode.BAD_REQUEST)
    return value


def _positive_id(value: int, message: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(message)
    return value


def _page(value: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("页码无效")
    return value


def _page_size(value: int) -> int:
    if type(value) is not int or not 1 <= value <= _PAGE_SIZE_MAX:
        raise ValueError("每页条数无效")
    return value


def _message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    text = error["msg"]
    prefix = "Value error, "
    if text.startswith(prefix):
        return text.removeprefix(prefix)
    if error["type"] == "int_type":
        return "数字字段格式无效"
    return "参数无效"
