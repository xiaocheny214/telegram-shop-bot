"""BEpusdt 客户端的创建位置。渠道地址和令牌在 system.payment，下单规则在 shop.order。"""

from bepusdt import BEpusdtClient


def create_client(api_url: str, api_token: str) -> BEpusdtClient:
    return BEpusdtClient(api_url=api_url, api_token=api_token)
