"""卡密导入的入参。格式由 Pydantic 校验，不负责数据库写入。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException

_TEXT_MAX = 1_000_000
_PATH_MAX = 512


class CardImport(BaseModel):
    """一批卡密正文。一行一条，空行忽略。"""

    model_config = ConfigDict(strict=True)

    product_id: int
    spec_id: int | None = None
    text: str

    @field_validator("product_id")
    @classmethod
    def check_product_id(cls, value: int) -> int:
        return _positive_id(value, "商品编号无效")

    @field_validator("spec_id")
    @classmethod
    def check_spec_id(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _positive_id(value, "规格编号无效")

    @field_validator("text")
    @classmethod
    def check_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("卡密内容不能为空")
        if len(value) > _TEXT_MAX:
            raise ValueError("卡密内容过大")
        return value


class CardImportFile(BaseModel):
    """从文本文件导入。只读取 .txt，一行一条。"""

    model_config = ConfigDict(strict=True)

    product_id: int
    spec_id: int | None = None
    path: str

    @field_validator("product_id")
    @classmethod
    def check_product_id(cls, value: int) -> int:
        return _positive_id(value, "商品编号无效")

    @field_validator("spec_id")
    @classmethod
    def check_spec_id(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _positive_id(value, "规格编号无效")

    @field_validator("path")
    @classmethod
    def check_path(cls, value: str) -> str:
        path = value.strip()
        if not path:
            raise ValueError("文件路径不能为空")
        if len(path) > _PATH_MAX:
            raise ValueError("文件路径过长")
        if any(ord(char) < 32 or ord(char) == 127 for char in path):
            raise ValueError("文件路径包含非法字符")
        return path


def parse_model[ModelT: BaseModel](model: type[ModelT], value: ModelT | dict[str, Any]) -> ModelT:
    if isinstance(value, model):
        return value
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise BizException(_message(exc), code=BizCode.BAD_REQUEST) from exc


def parse_card_id(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise BizException("卡密编号无效", code=BizCode.BAD_REQUEST)
    return value


def _positive_id(value: int, message: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(message)
    return value


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
    return "参数无效"
