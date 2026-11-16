"""用户入参和资料查询结果。格式由 Pydantic 校验，不负责数据库写入。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import UserRole
from shopbot.framework.exceptions.biz import BizException

_USERNAME_MAX = 255
_TELEGRAM_ID_MAX = 2**63 - 1


class UserCreate(BaseModel):
    model_config = ConfigDict(strict=True)

    telegram_id: int
    username: str | None = None
    role: int = UserRole.USER

    @field_validator("telegram_id")
    @classmethod
    def check_telegram_id(cls, value: int) -> int:
        return _telegram_id(value)

    @field_validator("username")
    @classmethod
    def check_username(cls, value: str | None) -> str | None:
        return _username(value)

    @field_validator("role")
    @classmethod
    def check_role(cls, value: int) -> int:
        if type(value) is not int or value not in UserRole._value2member_map_:
            raise ValueError("用户角色无效")
        return value


class UserProfile(BaseModel):
    """买家资料页的聚合结果。名字的展示由机器人用 Telegram 显示名决定。"""

    model_config = ConfigDict(strict=True)

    user_id: int
    telegram_id: int
    username: str | None
    balance_usdt_cents: int
    order_count: int
    created_at: datetime


def parse_user_id(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise BizException("用户编号无效", code=BizCode.BAD_REQUEST)
    return value


def parse_model[ModelT: BaseModel](model: type[ModelT], value: ModelT | dict[str, Any]) -> ModelT:
    if isinstance(value, model):
        return value
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise BizException(_message(exc), code=BizCode.BAD_REQUEST) from exc


def _telegram_id(value: int) -> int:
    if type(value) is not int or not 1 <= value <= _TELEGRAM_ID_MAX:
        raise ValueError("Telegram 编号无效")
    return value


def _username(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("用户名必须是文本")
    name = value.strip()
    if not name:
        return None
    if len(name) > _USERNAME_MAX:
        raise ValueError("用户名不能超过 255 个字符")
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("用户名包含非法字符")
    return name


def _message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    text = error["msg"]
    prefix = "Value error, "
    if text.startswith(prefix):
        return text.removeprefix(prefix)
    kind = error["type"]
    if kind == "string_type":
        return "用户名必须是文本"
    if kind == "int_type":
        return "数字字段格式无效"
    return "参数无效"
