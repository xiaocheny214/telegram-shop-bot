"""钱包入参。格式由 Pydantic 校验，不负责数据库写入。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException

_AMOUNT_MAX = 100_000_000
_REASON_MAX = 64
_REF_TYPE_MAX = 32
_REF_ID_MAX = 64


class RechargePageQuery(BaseModel):
    """某个买家的充值记录分页。"""

    model_config = ConfigDict(strict=True)

    user_id: int
    page: int = 1
    page_size: int = 5

    @field_validator("user_id")
    @classmethod
    def check_user_id(cls, value: int) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("用户编号无效")
        return value

    @field_validator("page")
    @classmethod
    def check_page(cls, value: int) -> int:
        if type(value) is not int or value < 1:
            raise ValueError("页码无效")
        return value

    @field_validator("page_size")
    @classmethod
    def check_page_size(cls, value: int) -> int:
        if type(value) is not int or not 1 <= value <= 20:
            raise ValueError("每页条数无效")
        return value


class RechargeCreate(BaseModel):
    """把一笔 USDT 充进钱包。实际付给渠道的金额按店铺汇率折算。"""

    model_config = ConfigDict(strict=True)

    user_id: int
    amount_usdt_cents: int
    channel_id: int
    network: int | None = None

    @field_validator("user_id")
    @classmethod
    def check_user_id(cls, value: int) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("用户编号无效")
        return value

    @field_validator("amount_usdt_cents")
    @classmethod
    def check_amount(cls, value: int) -> int:
        if type(value) is not int or not 1 <= value <= _AMOUNT_MAX:
            raise ValueError("金额无效")
        return value

    @field_validator("channel_id")
    @classmethod
    def check_channel_id(cls, value: int) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("支付渠道编号无效")
        return value

    @field_validator("network")
    @classmethod
    def check_network(cls, value: int | None) -> int | None:
        from shopbot.framework.enums.model import UsdtNetwork

        if value is None:
            return None
        if type(value) is not int or value not in UsdtNetwork._value2member_map_:
            raise ValueError("USDT 网络无效")
        return value


class WalletChange(BaseModel):
    """一笔余额变动。金额是正数的 USDT 分，方向由入账或出账方法决定。"""

    model_config = ConfigDict(strict=True)

    user_id: int
    amount_usdt_cents: int
    reason: str
    ref_type: str | None = None
    ref_id: str | None = None
    operator_user_id: int | None = None

    @field_validator("user_id")
    @classmethod
    def check_user_id(cls, value: int) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("用户编号无效")
        return value

    @field_validator("amount_usdt_cents")
    @classmethod
    def check_amount(cls, value: int) -> int:
        if type(value) is not int or not 1 <= value <= _AMOUNT_MAX:
            raise ValueError("金额无效")
        return value

    @field_validator("reason")
    @classmethod
    def check_reason(cls, value: str) -> str:
        return _text(value, "原因", _REASON_MAX, required=True)

    @field_validator("ref_type")
    @classmethod
    def check_ref_type(cls, value: str | None) -> str | None:
        return _text(value, "关联类型", _REF_TYPE_MAX, required=False)

    @field_validator("ref_id")
    @classmethod
    def check_ref_id(cls, value: str | None) -> str | None:
        return _text(value, "关联编号", _REF_ID_MAX, required=False)

    @field_validator("operator_user_id")
    @classmethod
    def check_operator(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if type(value) is not int or value <= 0:
            raise ValueError("用户编号无效")
        return value

    @model_validator(mode="after")
    def check_ref(self) -> "WalletChange":
        if (self.ref_type is None) != (self.ref_id is None):
            raise ValueError("关联类型和关联编号需要同时填写")
        return self


def parse_model[ModelT: BaseModel](model: type[ModelT], value: ModelT | dict[str, Any]) -> ModelT:
    if isinstance(value, model):
        return value
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise BizException(_message(exc), code=BizCode.BAD_REQUEST) from exc


def _text(value: str | None, label: str, limit: int, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{label}不能为空")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label}必须是文本")
    text = value.strip()
    if not text:
        if required:
            raise ValueError(f"{label}不能为空")
        return None
    if len(text) > limit:
        raise ValueError(f"{label}过长")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError(f"{label}包含非法字符")
    return text


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
