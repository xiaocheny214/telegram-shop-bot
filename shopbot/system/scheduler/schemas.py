"""修改定时任务的入参。间隔和启停由管理员决定，任务代码不能改。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException

_INTERVAL_MAX = 86_400


class ScheduledTaskUpdate(BaseModel):
    model_config = ConfigDict(strict=True)

    interval_seconds: int
    is_active: bool

    @field_validator("interval_seconds")
    @classmethod
    def check_interval(cls, value: int) -> int:
        if type(value) is not int or not 1 <= value <= _INTERVAL_MAX:
            raise ValueError("任务间隔超出范围")
        return value

    @field_validator("is_active")
    @classmethod
    def check_active(cls, value: bool) -> bool:
        if type(value) is not bool:
            raise ValueError("启用状态无效")
        return value


def parse_model[ModelT: BaseModel](model: type[ModelT], value: ModelT | dict[str, Any]) -> ModelT:
    if isinstance(value, model):
        return value
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise BizException(_message(exc), code=BizCode.BAD_REQUEST) from exc


def parse_task_code(value: str) -> str:
    if not isinstance(value, str):
        raise BizException("定时任务不存在", code=BizCode.NOT_FOUND)
    code = value.strip()
    if not code or len(code) > 64:
        raise BizException("定时任务不存在", code=BizCode.NOT_FOUND)
    return code


def _message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    text = error["msg"]
    prefix = "Value error, "
    if text.startswith(prefix):
        return text.removeprefix(prefix)
    if error["type"] == "int_type":
        return "数字字段格式无效"
    if error["type"] == "bool_type":
        return "启用状态无效"
    return "参数无效"
