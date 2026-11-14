"""用 X25519 公钥加密机密，用对应私钥解密。

同一原文和同一私钥得到同一密文，方便按密文去重。
一次性密钥和随机数都由私钥与原文推导，不再随机生成。
以前随机加密写下的密文仍然可以解密。
数据库泄露时没有私钥就解不开。拿到这台机器上的环境变量则仍然可以解开。
"""

import base64
import hmac
import os
from hashlib import sha256

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException

_PREFIX = "enc1:"
_INFO = b"shopbot-payment-v1"
_PUBLIC_LEN = 32
_NONCE_LEN = 12


def generate_private_key_pem() -> str:
    """生成 PKCS8 PEM。放在 .env 的 PAYMENT_PRIVATE_KEY 里，不要写入数据库或仓库。"""
    key = X25519PrivateKey.generate()
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def public_key_pem(private_pem: str) -> str:
    key = _load_private(private_pem)
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def seal(plain: str, private_pem: str) -> str:
    """用私钥推导出的公钥加密。空字符串保持为空。相同原文得到相同密文。"""
    if plain == "":
        return ""
    plain_bytes = plain.encode()
    master = _load_private(private_pem)
    seed = hmac.new(_raw_private(master), b"shopbot-ephemeral-v1" + plain_bytes, sha256).digest()
    ephemeral = X25519PrivateKey.from_private_bytes(seed)
    shared = ephemeral.exchange(master.public_key())
    nonce = hmac.new(seed, b"shopbot-nonce-v1" + plain_bytes, sha256).digest()[:_NONCE_LEN]
    ciphertext = AESGCM(_derive(shared)).encrypt(nonce, plain_bytes, _INFO)
    ephemeral_public = ephemeral.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    blob = ephemeral_public + nonce + ciphertext
    return _PREFIX + base64.b64encode(blob).decode()


def open_secret(stored: str, private_pem: str) -> str:
    """解密。没有 enc1 前缀的旧数据按原文返回，便于已有渠道继续使用。"""
    if stored == "" or not stored.startswith(_PREFIX):
        return stored
    try:
        raw = base64.b64decode(stored.removeprefix(_PREFIX), validate=True)
        if len(raw) <= _PUBLIC_LEN + _NONCE_LEN:
            raise ValueError("密文过短")
        ephemeral_public = X25519PublicKey.from_public_bytes(raw[:_PUBLIC_LEN])
        nonce = raw[_PUBLIC_LEN : _PUBLIC_LEN + _NONCE_LEN]
        ciphertext = raw[_PUBLIC_LEN + _NONCE_LEN :]
        shared = _load_private(private_pem).exchange(ephemeral_public)
        plain = AESGCM(_derive(shared)).decrypt(nonce, ciphertext, _INFO)
        return plain.decode()
    except Exception as exc:
        raise BizException("库内密文无法解密", code=BizCode.INTERNAL_ERROR) from exc


def load_private_key_pem() -> str:
    pem = os.environ.get("PAYMENT_PRIVATE_KEY", "").strip().replace("\\n", "\n")
    if not pem:
        raise RuntimeError("缺少环境变量 PAYMENT_PRIVATE_KEY")
    _load_private(pem)
    return pem


def _load_private(pem: str) -> X25519PrivateKey:
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, X25519PrivateKey):
        raise RuntimeError("支付私钥必须是 X25519")
    return key


def _raw_private(key: X25519PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _derive(shared: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(shared)
