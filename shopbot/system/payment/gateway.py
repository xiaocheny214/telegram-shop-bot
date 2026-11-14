"""渠道下单结果的统一形状。各渠道自己的字段在这里折成订单详情能存的四项。

BEpusdt 创建订单后：
trade_id 写入 channel_trade_no，actual_amount 写入 pay_amount，
token 收款地址写入 pay_target，payment_url 写入 pay_url。
请求里的 trade_type 由 UsdtNetwork 转成 usdt.trc20、usdt.erc20、usdt.solana、usdt.polygon。
amount 是法币金额，买家实付以 actual_amount 为准。
verify_callback 只证明签名来自该网关。状态 2 才是支付成功，
还要核对本地订单号和金额，并且同一个订单只履约一次。

支付宝、微信、易支付和 BEpusdt 都收成这四项。
前三个没有 USDT 网络，订单的 pay_network 留空。
"""

from dataclasses import dataclass

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import UsdtNetwork
from shopbot.framework.exceptions.biz import BizException

_USDT_TRADE_TYPE = {
    UsdtNetwork.TRC20: "usdt.trc20",
    UsdtNetwork.ERC20: "usdt.erc20",
    UsdtNetwork.SOLANA: "usdt.solana",
    UsdtNetwork.POLYGON: "usdt.polygon",
}


@dataclass(frozen=True)
class ChannelPayment:
    channel_trade_no: str
    pay_amount: str
    pay_target: str | None
    pay_url: str | None


def usdt_trade_type(network: UsdtNetwork | int) -> str:
    if isinstance(network, UsdtNetwork):
        return _USDT_TRADE_TYPE[network]
    if type(network) is int and network in UsdtNetwork._value2member_map_:
        return _USDT_TRADE_TYPE[UsdtNetwork(network)]
    raise BizException("USDT 网络无效", code=BizCode.BAD_REQUEST)
