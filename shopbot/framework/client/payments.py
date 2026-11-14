"""BEpusdt 客户端的创建位置。渠道地址和令牌在 system.payment，下单规则在 shop.order。

当前网关没有签名查单接口。回调入账前读取 POST /api/v1/pay/info。
这个接口不验商户签名，只用来核对已经验过签的成功回调。
"""

import json
from urllib.request import Request

from bepusdt import BEpusdtClient


def create_client(api_url: str, api_token: str) -> BEpusdtClient:
    return BEpusdtClient(api_url=api_url, api_token=api_token)


def trade_info_request(api_url: str, trade_id: str) -> Request:
    """收银台订单详情。请求体只有 trade_id。返回里的 status、money、actual_amount、order_id 是网关当前订单。"""
    body = json.dumps({"trade_id": trade_id}, separators=(",", ":")).encode()
    return Request(
        api_url.rstrip("/") + "/api/v1/pay/info",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
