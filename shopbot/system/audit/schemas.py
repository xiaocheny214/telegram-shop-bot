"""审计查询条件。分页有上限，避免一次载入全部记录。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException

_PAGE_MAX = 100
_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 50
_ACTION_MAX = 128
_STATUSES = {200, 400, 401, 403, 500}


class AuditQuery(BaseModel):
    """条件都可空。页码和每页条数始终限制结果数量。"""

    model_config = ConfigDict(strict=True)

    created_from: datetime | None = None
    created_to: datetime | None = None
    operator_user_id: int | None = None
    operator_telegram_id: int | None = None
    action: str | None = None
    status_code: int | None = None
    page: int = 1
    page_size: int = _PAGE_SIZE_DEFAULT

    @field_validator("created_from", "created_to")
    @classmethod
    def check_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间必须带时区")
        return value

    @field_validator("operator_user_id", "operator_telegram_id")
    @classmethod
    def check_operator(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if type(value) is not int or value <= 0:
            raise ValueError("操作者编号无效")
        return value

    @field_validator("action")
    @classmethod
    def check_action(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if type(value) is not str:
            raise ValueError("动作无效")
        action = value.strip()
        if not action or len(action) > _ACTION_MAX:
            raise ValueError("动作无效")
        return action

    @field_validator("status_code")
    @classmethod
    def check_status(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if type(value) is not int or value not in _STATUSES:
            raise ValueError("状态码无效")
        return value

    @field_validator("page")
    @classmethod
    def check_page(cls, value: int) -> int:
        if type(value) is not int or not 1 <= value <= _PAGE_MAX:
            raise ValueError("页码超出范围")
        return value

    @field_validator("page_size")
    @classmethod
    def check_page_size(cls, value: int) -> int:
        if type(value) is not int or not 1 <= value <= _PAGE_SIZE_MAX:
            raise ValueError("每页条数超出范围")
        return value

    @model_validator(mode="after")
    def check_range(self) -> "AuditQuery":
        if self.created_from is not None and self.created_to is not None and self.created_from > self.created_to:
            raise ValueError("开始时间不能晚于结束时间")
        return self


def parse_audit_query(value: AuditQuery | dict[str, Any]) -> AuditQuery:
    if isinstance(value, AuditQuery):
        return value
    try:
        return AuditQuery.model_validate(value)
    except ValidationError as exc:
        raise BizException(_message(exc), code=BizCode.BAD_REQUEST) from exc


def _message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    text = error["msg"]
    prefix = "Value error, "
    if text.startswith(prefix):
        return text.removeprefix(prefix)
    return "查询条件无效"
