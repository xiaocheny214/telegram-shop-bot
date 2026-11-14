"""商品和规格的入参。格式由 Pydantic 校验，不负责数据库写入。"""

from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import Currency, DeliveryMethod, StockDeductStrategy
from shopbot.framework.exceptions.biz import BizException

_NAME_MAX = 128
_SPEC_NAME_MAX = 64
_REMARK_MAX = 128
_DESCRIPTION_MAX = 4_000
_URL_MAX = 512
_SORT_MAX = 10_000
_PRICE_MAX = 100_000_000
_STOCK_MAX = 1_000_000


class ProductCreate(BaseModel):
    model_config = ConfigDict(strict=True)

    category_id: int
    name: str
    cover_url: str | None = None
    currency: int = Currency.USDT
    price_cents: int
    delivery_method: int = DeliveryMethod.AUTO
    stock_deduct_strategy: int = StockDeductStrategy.DAILY
    stock: int = 0
    sort_order: int = 0
    is_active: bool = True
    description: str = ""
    internal_remark: str | None = None

    @field_validator("category_id")
    @classmethod
    def check_category_id(cls, value: int) -> int:
        return _positive_id(value, "分类编号无效")

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        return _text(value, empty="商品名称不能为空", too_long="商品名称不能超过 128 个字符", limit=_NAME_MAX)

    @field_validator("cover_url")
    @classmethod
    def check_cover(cls, value: str | None) -> str | None:
        return _cover_url(value)

    @field_validator("currency")
    @classmethod
    def check_currency(cls, value: int) -> int:
        return _enum_value(value, Currency, "货币无效")

    @field_validator("price_cents")
    @classmethod
    def check_price(cls, value: int) -> int:
        return _price(value)

    @field_validator("delivery_method")
    @classmethod
    def check_delivery(cls, value: int) -> int:
        return _enum_value(value, DeliveryMethod, "发货方式无效")

    @field_validator("stock_deduct_strategy")
    @classmethod
    def check_stock_strategy(cls, value: int) -> int:
        return _enum_value(value, StockDeductStrategy, "库存扣减策略无效")

    @field_validator("stock")
    @classmethod
    def check_stock(cls, value: int) -> int:
        return _stock(value)

    @field_validator("sort_order")
    @classmethod
    def check_sort_order(cls, value: int) -> int:
        return _sort_order(value)

    @field_validator("is_active")
    @classmethod
    def check_active(cls, value: bool) -> bool:
        return _active(value)

    @field_validator("description")
    @classmethod
    def check_description(cls, value: str) -> str:
        return _optional_text(value, limit=_DESCRIPTION_MAX, too_long="使用说明不能超过 4000 个字符") or ""

    @field_validator("internal_remark")
    @classmethod
    def check_remark(cls, value: str | None) -> str | None:
        return _optional_text(value, limit=_REMARK_MAX, too_long="内部备注不能超过 128 个字符")


class ProductUpdate(BaseModel):
    model_config = ConfigDict(strict=True)

    category_id: int | None = None
    name: str | None = None
    cover_url: str | None = None
    currency: int | None = None
    price_cents: int | None = None
    delivery_method: int | None = None
    stock_deduct_strategy: int | None = None
    stock: int | None = None
    sort_order: int | None = None
    is_active: bool | None = None
    description: str | None = None
    internal_remark: str | None = None

    @field_validator("category_id")
    @classmethod
    def check_category_id(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _positive_id(value, "分类编号无效")

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, empty="商品名称不能为空", too_long="商品名称不能超过 128 个字符", limit=_NAME_MAX)

    @field_validator("cover_url")
    @classmethod
    def check_cover(cls, value: str | None) -> str | None:
        return _cover_url(value)

    @field_validator("currency")
    @classmethod
    def check_currency(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _enum_value(value, Currency, "货币无效")

    @field_validator("price_cents")
    @classmethod
    def check_price(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _price(value)

    @field_validator("delivery_method")
    @classmethod
    def check_delivery(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _enum_value(value, DeliveryMethod, "发货方式无效")

    @field_validator("stock_deduct_strategy")
    @classmethod
    def check_stock_strategy(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _enum_value(value, StockDeductStrategy, "库存扣减策略无效")

    @field_validator("stock")
    @classmethod
    def check_stock(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _stock(value)

    @field_validator("sort_order")
    @classmethod
    def check_sort_order(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _sort_order(value)

    @field_validator("is_active")
    @classmethod
    def check_active(cls, value: bool | None) -> bool | None:
        if value is None:
            return None
        return _active(value)

    @field_validator("description")
    @classmethod
    def check_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _optional_text(value, limit=_DESCRIPTION_MAX, too_long="使用说明不能超过 4000 个字符") or ""

    @field_validator("internal_remark")
    @classmethod
    def check_remark(cls, value: str | None) -> str | None:
        return _optional_text(value, limit=_REMARK_MAX, too_long="内部备注不能超过 128 个字符")

    @model_validator(mode="after")
    def has_change(self) -> "ProductUpdate":
        if not self.model_fields_set:
            raise ValueError("没有要修改的内容")
        return self


class SpecCreate(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str
    price_cents: int
    stock: int = 0
    sort_order: int = 0
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        return _text(value, empty="规格名称不能为空", too_long="规格名称不能超过 64 个字符", limit=_SPEC_NAME_MAX)

    @field_validator("price_cents")
    @classmethod
    def check_price(cls, value: int) -> int:
        return _price(value)

    @field_validator("stock")
    @classmethod
    def check_stock(cls, value: int) -> int:
        return _stock(value)

    @field_validator("sort_order")
    @classmethod
    def check_sort_order(cls, value: int) -> int:
        return _sort_order(value)

    @field_validator("is_active")
    @classmethod
    def check_active(cls, value: bool) -> bool:
        return _active(value)


class SpecUpdate(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str | None = None
    price_cents: int | None = None
    stock: int | None = None
    sort_order: int | None = None
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, empty="规格名称不能为空", too_long="规格名称不能超过 64 个字符", limit=_SPEC_NAME_MAX)

    @field_validator("price_cents")
    @classmethod
    def check_price(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _price(value)

    @field_validator("stock")
    @classmethod
    def check_stock(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _stock(value)

    @field_validator("sort_order")
    @classmethod
    def check_sort_order(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _sort_order(value)

    @field_validator("is_active")
    @classmethod
    def check_active(cls, value: bool | None) -> bool | None:
        if value is None:
            return None
        return _active(value)

    @model_validator(mode="after")
    def has_change(self) -> "SpecUpdate":
        if all(item is None for item in (self.name, self.price_cents, self.stock, self.sort_order, self.is_active)):
            raise ValueError("没有要修改的内容")
        return self


def parse_product_id(value: int) -> int:
    return _id(value, "商品编号无效")


def parse_spec_id(value: int) -> int:
    return _id(value, "规格编号无效")


def parse_model[ModelT: BaseModel](model: type[ModelT], value: ModelT | dict[str, Any]) -> ModelT:
    if isinstance(value, model):
        return value
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise BizException(_message(exc), code=BizCode.BAD_REQUEST) from exc


def _id(value: int, message: str) -> int:
    if type(value) is not int or value <= 0:
        raise BizException(message, code=BizCode.BAD_REQUEST)
    return value


def _positive_id(value: int, message: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(message)
    return value


def _text(value: str, *, empty: str, too_long: str, limit: int) -> str:
    text = value.strip()
    if not text:
        raise ValueError(empty)
    if len(text) > limit:
        raise ValueError(too_long)
    _reject_controls(text)
    return text


def _optional_text(value: str | None, *, limit: int, too_long: str) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(too_long)
    _reject_controls(text)
    return text


def _cover_url(value: str | None) -> str | None:
    if value is None:
        return None
    url = value.strip()
    if not url:
        return None
    if len(url) > _URL_MAX:
        raise ValueError("封面地址不能超过 512 个字符")
    _reject_controls(url)
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("封面地址必须是 http 或 https")
    return url


def _price(value: int) -> int:
    if type(value) is not int:
        raise ValueError("价格必须是整数")
    if not 1 <= value <= _PRICE_MAX:
        raise ValueError("价格超出范围")
    return value


def _stock(value: int) -> int:
    if type(value) is not int:
        raise ValueError("库存必须是整数")
    if not 0 <= value <= _STOCK_MAX:
        raise ValueError("库存超出范围")
    return value


def _sort_order(value: int) -> int:
    if type(value) is not int:
        raise ValueError("排序必须是整数")
    if not 0 <= value <= _SORT_MAX:
        raise ValueError("排序超出范围")
    return value


def _active(value: bool) -> bool:
    if type(value) is not bool:
        raise ValueError("上下架状态无效")
    return value


def _enum_value(
    value: int,
    enum: type[Currency] | type[DeliveryMethod] | type[StockDeductStrategy],
    message: str,
) -> int:
    if type(value) is not int or value not in enum._value2member_map_:
        raise ValueError(message)
    return value


def _reject_controls(value: str) -> None:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("内容包含非法字符")


def _message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    text = error["msg"]
    prefix = "Value error, "
    if text.startswith(prefix):
        return text.removeprefix(prefix)
    kind = error["type"]
    if kind == "string_type":
        return "文本字段格式无效"
    if kind == "int_type":
        return "数字字段格式无效"
    if kind == "bool_type":
        return "上下架状态无效"
    return "参数无效"
