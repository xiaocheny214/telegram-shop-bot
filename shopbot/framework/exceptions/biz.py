"""业务异常。领域代码 raise BizException，由 bootstrap.handler 转成响应。"""

from shopbot.framework.enums.biz_code import BizCode


class BizException(Exception):
    def __init__(
        self,
        message: str = "业务异常",
        *,
        code: int = BizCode.INTERNAL_ERROR,
        data: object = None,
    ) -> None:
        self.message = message
        self.code = code
        self.data = data
        super().__init__(message)
