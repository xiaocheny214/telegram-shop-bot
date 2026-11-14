"""新增支付渠道的入参。格式由 Pydantic 校验，不负责数据库写入。"""

from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import EpayMethod, PaymentProvider
from shopbot.framework.exceptions.biz import BizException

_URL_MAX = 512
_ID_MAX = 64
_SECRET_MAX = 8_000
_SORT_MAX = 10_000
_CONFIGURABLE = {PaymentProvider.ALIPAY, PaymentProvider.WECHAT, PaymentProvider.EPAY, PaymentProvider.BEPUSDT}


class PaymentChannelCreate(BaseModel):
    """选择供应商后填写该供应商的配置。排序和启用状态各供应商都有。"""

    model_config = ConfigDict(strict=True)

    provider: int
    sort_order: int = 0
    is_active: bool = True
    notify_url: str = ""
    app_id: str | None = None
    private_key: str | None = None
    alipay_public_key: str | None = None
    api_url: str | None = None
    merchant_id: str | None = None
    api_v3_key: str | None = None
    cert_serial: str | None = None
    private_key_path: str | None = None
    epay_method: int | None = None
    merchant_key: str | None = None
    return_url: str | None = None
    api_token: str | None = None

    @field_validator("provider")
    @classmethod
    def check_provider(cls, value: int) -> int:
        if type(value) is not int or value not in PaymentProvider._value2member_map_:
            raise ValueError("支付渠道无效")
        if PaymentProvider(value) not in _CONFIGURABLE:
            raise ValueError("请选择支付宝、微信、易支付或 BEpusdt")
        return value

    @field_validator("sort_order")
    @classmethod
    def check_sort_order(cls, value: int) -> int:
        if type(value) is not int or not 0 <= value <= _SORT_MAX:
            raise ValueError("排序超出范围")
        return value

    @field_validator("is_active")
    @classmethod
    def check_active(cls, value: bool) -> bool:
        if type(value) is not bool:
            raise ValueError("启用状态无效")
        return value

    @field_validator("notify_url")
    @classmethod
    def check_notify_url(cls, value: str) -> str:
        if not value.strip():
            return ""
        return _required_https(value, "异步回调地址")

    @field_validator("app_id")
    @classmethod
    def check_app_id(cls, value: str | None) -> str | None:
        return _optional_text(value, "应用编号", _ID_MAX)

    @field_validator("private_key")
    @classmethod
    def check_private_key(cls, value: str | None) -> str | None:
        return _optional_text(value, "应用私钥", _SECRET_MAX, allow_newline=True)

    @field_validator("alipay_public_key")
    @classmethod
    def check_alipay_public_key(cls, value: str | None) -> str | None:
        return _optional_text(value, "支付宝公钥", _SECRET_MAX, allow_newline=True)

    @field_validator("api_url")
    @classmethod
    def check_api_url(cls, value: str | None) -> str | None:
        return _optional_https(value, "网关地址")

    @field_validator("merchant_id")
    @classmethod
    def check_merchant_id(cls, value: str | None) -> str | None:
        return _optional_text(value, "商户号", _ID_MAX)

    @field_validator("api_v3_key")
    @classmethod
    def check_api_v3_key(cls, value: str | None) -> str | None:
        return _optional_text(value, "APIv3 密钥", 128)

    @field_validator("cert_serial")
    @classmethod
    def check_cert_serial(cls, value: str | None) -> str | None:
        return _optional_text(value, "证书序列号", 128)

    @field_validator("private_key_path")
    @classmethod
    def check_private_key_path(cls, value: str | None) -> str | None:
        return _optional_text(value, "私钥文件路径", _URL_MAX)

    @field_validator("epay_method")
    @classmethod
    def check_epay_method(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if type(value) is not int or value not in EpayMethod._value2member_map_:
            raise ValueError("易支付方式无效")
        return value

    @field_validator("merchant_key")
    @classmethod
    def check_merchant_key(cls, value: str | None) -> str | None:
        return _optional_text(value, "商户密钥", 256)

    @field_validator("return_url")
    @classmethod
    def check_return_url(cls, value: str | None) -> str | None:
        return _optional_https(value, "同步跳转地址")

    @field_validator("api_token")
    @classmethod
    def check_api_token(cls, value: str | None) -> str | None:
        return _optional_text(value, "网关令牌", 256)

    @model_validator(mode="after")
    def check_provider_fields(self) -> "PaymentChannelCreate":
        provider = PaymentProvider(self.provider)
        if provider is PaymentProvider.ALIPAY:
            _need(self.app_id, "应用 appId 不能为空")
            _need(self.private_key, "应用私钥不能为空")
            _need(self.alipay_public_key, "支付宝公钥不能为空")
            _need(self.api_url, "网关地址不能为空")
            if self.epay_method is not None:
                raise ValueError("支付宝不需要选择易支付方式")
        elif provider is PaymentProvider.WECHAT:
            _need(self.app_id, "APPID 不能为空")
            _need(self.merchant_id, "商户号不能为空")
            _need(self.api_v3_key, "APIv3 密钥不能为空")
            _need(self.cert_serial, "证书序列号不能为空")
            _need(self.private_key_path, "私钥文件路径不能为空")
            if self.epay_method is not None:
                raise ValueError("微信不需要选择易支付方式")
        elif provider is PaymentProvider.EPAY:
            if self.epay_method is None:
                raise ValueError("请选择易支付的支付宝或微信")
            _need(self.merchant_id, "商户 ID 不能为空")
            _need(self.merchant_key, "商户密钥不能为空")
            _need(self.api_url, "api 地址不能为空")
            _need(self.return_url, "同步跳转地址不能为空")
        elif provider is PaymentProvider.BEPUSDT:
            _need(self.api_url, "网关地址不能为空")
            _need(self.api_token, "网关令牌不能为空")
            if self.epay_method is not None:
                raise ValueError("BEpusdt 不需要选择易支付方式")
        if provider is not PaymentProvider.BEPUSDT and not self.notify_url:
            raise ValueError("异步回调地址不能为空")
        return self


def parse_model[ModelT: BaseModel](model: type[ModelT], value: ModelT | dict[str, Any]) -> ModelT:
    if isinstance(value, model):
        return value
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise BizException(_message(exc), code=BizCode.BAD_REQUEST) from exc


def parse_channel_id(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise BizException("支付渠道编号无效", code=BizCode.BAD_REQUEST)
    return value


def _need(value: str | None, message: str) -> None:
    if value is None:
        raise ValueError(message)


def _required_https(value: str, label: str) -> str:
    url = _optional_https(value, label)
    if url is None:
        raise ValueError(f"{label}不能为空")
    return url


def _optional_https(value: str | None, label: str) -> str | None:
    url = _optional_text(value, label, _URL_MAX)
    if url is None:
        return None
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{label}必须是 https")
    return url


def _optional_text(value: str | None, label: str, limit: int, *, allow_newline: bool = False) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(f"{label}过长")
    for char in text:
        code = ord(char)
        if code == 127 or (code < 32 and not (allow_newline and char in "\n\r\t")):
            raise ValueError("内容包含非法字符")
    return text


def _message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    text = error["msg"]
    prefix = "Value error, "
    if text.startswith(prefix):
        return text.removeprefix(prefix)
    if error["type"] == "string_type":
        return "文本字段格式无效"
    if error["type"] == "int_type":
        return "数字字段格式无效"
    if error["type"] == "bool_type":
        return "启用状态无效"
    return "参数无效"
