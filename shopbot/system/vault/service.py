"""把机密写入数据库前加密，读出来再解密。

私钥在环境变量里。加密时由它推导公钥，读的时候只用私钥。
相同原文得到相同密文，卡密可以直接按密文判断重复。
编号、状态、金额、外键保持明文，否则查单、扣库存和对账会失效。
会话里的对象解密后标成未修改，避免下一次提交把明文写回去。
"""

from sqlalchemy.orm.attributes import set_committed_value

from shopbot.framework.config.app import get_app_settings
from shopbot.framework.crypto.secrets import open_secret, seal


def protect(plain: str | None) -> str | None:
    """加密后落库。空值保持空值。"""
    if plain is None or plain == "":
        return plain
    return seal(plain, _private_key())


def reveal(stored: str | None) -> str | None:
    """解密给业务使用。没有密文标记的旧数据按原文返回。"""
    if stored is None or stored == "":
        return stored
    return open_secret(stored, _private_key())


def show(instance: object, name: str, plain: str | None) -> None:
    """把解密结果放进对象，但不记成待写入。"""
    set_committed_value(instance, name, plain)


def _private_key() -> str:
    return get_app_settings().payment_private_key
