"""分类入参。格式由 Pydantic 校验，不负责数据库写入。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException

_NAME_MAX = 128
_SORT_MAX = 10_000


class CategoryCreate(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str
    sort_order: int = 0
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        return _name(value)

    @field_validator("sort_order")
    @classmethod
    def check_sort_order(cls, value: int) -> int:
        return _sort_order(value)

    @field_validator("is_active")
    @classmethod
    def check_active(cls, value: bool) -> bool:
        return _active(value)


class CategoryUpdate(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _name(value)

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
    def has_change(self) -> "CategoryUpdate":
        if self.name is None and self.sort_order is None and self.is_active is None:
            raise ValueError("没有要修改的内容")
        return self


def parse_category_id(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise BizException("分类编号无效", code=BizCode.BAD_REQUEST)
    return value


def parse_model[ModelT: BaseModel](model: type[ModelT], value: ModelT | dict[str, Any]) -> ModelT:
    if isinstance(value, model):
        return value
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise BizException(_message(exc), code=BizCode.BAD_REQUEST) from exc


def _name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError("分类名称不能为空")
    if len(name) > _NAME_MAX:
        raise ValueError("分类名称不能超过 128 个字符")
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("分类名称包含非法字符")
    return name


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


def _message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    text = error["msg"]
    prefix = "Value error, "
    if text.startswith(prefix):
        return text.removeprefix(prefix)
    kind = error["type"]
    if kind == "string_type":
        return "分类名称必须是文本"
    if kind == "int_type":
        return "排序必须是整数"
    if kind == "bool_type":
        return "上下架状态无效"
    return "参数无效"
