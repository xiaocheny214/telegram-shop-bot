"""店铺设置入参。格式由 Pydantic 校验，不负责数据库写入。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException

_TIMEOUT_MIN = 60
_TIMEOUT_MAX = 86_400
_PENDING_MAX = 100
_THANKS_MAX = 1_000
_RATE_MAX = 100_000_000


class ShopSettingUpdate(BaseModel):
    """只改传入的字段。感谢语传空字符串表示清空。"""

    model_config = ConfigDict(strict=True)

    order_timeout_seconds: int | None = None
    max_pending_orders: int | None = None
    thank_you_message: str | None = None
    usd_cny_cents: int | None = None
    usdt_cny_cents: int | None = None
    gbp_cny_cents: int | None = None
    eur_cny_cents: int | None = None

    @field_validator("order_timeout_seconds")
    @classmethod
    def check_timeout(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if type(value) is not int or not _TIMEOUT_MIN <= value <= _TIMEOUT_MAX:
            raise ValueError("订单超时必须在 60 到 86400 秒之间")
        return value

    @field_validator("max_pending_orders")
    @classmethod
    def check_pending(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if type(value) is not int or not 1 <= value <= _PENDING_MAX:
            raise ValueError("待支付上限必须在 1 到 100 之间")
        return value

    @field_validator("thank_you_message")
    @classmethod
    def check_thanks(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("感谢语必须是文本")
        text = value.strip()
        if not text:
            return None
        if len(text) > _THANKS_MAX:
            raise ValueError("感谢语不能超过 1000 个字符")
        for char in text:
            code = ord(char)
            if code == 127 or (code < 32 and char not in "\n\r\t"):
                raise ValueError("感谢语包含非法字符")
        return text

    @field_validator("usd_cny_cents", "usdt_cny_cents", "gbp_cny_cents", "eur_cny_cents")
    @classmethod
    def check_rate(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if type(value) is not int or not 0 <= value <= _RATE_MAX:
            raise ValueError("汇率超出范围")
        return value

    @model_validator(mode="after")
    def has_change(self) -> "ShopSettingUpdate":
        if not self.model_fields_set:
            raise ValueError("没有要修改的内容")
        return self


def parse_model[ModelT: BaseModel](model: type[ModelT], value: ModelT | dict[str, Any]) -> ModelT:
    if isinstance(value, model):
        return value
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise BizException(_message(exc), code=BizCode.BAD_REQUEST) from exc


def _message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    text = error["msg"]
    prefix = "Value error, "
    if text.startswith(prefix):
        return text.removeprefix(prefix)
    if error["type"] == "int_type":
        return "数字字段格式无效"
    if error["type"] == "string_type":
        return "文本字段格式无效"
    return "参数无效"
