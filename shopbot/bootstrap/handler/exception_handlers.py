"""把 BizException 转成 HTTP 响应。"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizException)
    async def handle_biz(_request: Request, exc: BizException) -> JSONResponse:
        status_code = 400
        if exc.code == BizCode.UNAUTHORIZED:
            status_code = 401
        elif exc.code == BizCode.FORBIDDEN:
            status_code = 403
        return JSONResponse(
            status_code=status_code,
            content={"code": exc.code, "message": exc.message, "data": exc.data},
        )
