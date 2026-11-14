"""待发货列表的分页入参。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException

_PAGE_SIZE_MAX = 20


class DeliveryPageQuery(BaseModel):
    model_config = ConfigDict(strict=True)

    page: int = 1
    page_size: int = 5

    @field_validator("page")
    @classmethod
    def check_page(cls, value: int) -> int:
        if type(value) is not int or value < 1:
            raise ValueError("页码无效")
        return value

    @field_validator("page_size")
    @classmethod
    def check_page_size(cls, value: int) -> int:
        if type(value) is not int or not 1 <= value <= _PAGE_SIZE_MAX:
            raise ValueError("每页条数无效")
        return value


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
    return "参数无效"
