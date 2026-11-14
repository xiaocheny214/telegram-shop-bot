"""支付回调入口。先验签，再向渠道查单，通过后才发货。"""

import json
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.order.service import accept_payment_callback

router = APIRouter()


@router.post("/payments/notify/{channel_id}")
async def notify(channel_id: int, request: Request) -> Response:
    raw = await request.body()
    try:
        form, data = _callback_body(raw, request.headers.get("content-type", ""))
        ack = await accept_payment_callback(
            request.app.state.session_factory,
            channel_id,
            form=form,
            data=data,
        )
    except BizException:
        return JSONResponse(status_code=400, content={"code": "FAIL", "message": "失败"})
    if ack.startswith("{"):
        return Response(content=ack, media_type="application/json")
    return PlainTextResponse(ack)


def _callback_body(raw: bytes, content_type: str) -> tuple[dict[str, str] | None, dict | None]:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise BizException("回调无效") from exc
    if "json" in content_type:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BizException("回调无效") from exc
        if not isinstance(payload, dict):
            raise BizException("回调无效")
        return None, payload
    return dict(parse_qsl(text, keep_blank_values=True)), None
