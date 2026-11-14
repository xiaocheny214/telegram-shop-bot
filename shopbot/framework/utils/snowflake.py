"""雪花订单号。

64 位正整数：41 位毫秒时间、10 位机器号、12 位同一毫秒内的序号。
时间从 2026-01-01 00:00:00 UTC 起算。同一毫秒内一台机器最多 4096 个号。
机器号来自环境变量 SNOWFLAKE_WORKER_ID，范围 0 到 1023。每个进程用不同的号，集群里才不会撞单。
时钟回拨时拒绝发号，避免把已经发出的号再发一次。
"""

import os
import threading
import time

_EPOCH_MS = 1767225600000  # 2026-01-01 00:00:00 UTC
_WORKER_BITS = 10
_SEQUENCE_BITS = 12
_WORKER_SHIFT = _SEQUENCE_BITS
_TIMESTAMP_SHIFT = _WORKER_BITS + _SEQUENCE_BITS
_MAX_WORKER_ID = (1 << _WORKER_BITS) - 1
_MAX_SEQUENCE = (1 << _SEQUENCE_BITS) - 1

_guard = threading.Lock()
_generator: "Snowflake | None" = None


class SnowflakeError(RuntimeError):
    """发号失败。时钟回拨或机器号无效时抛出。"""


class Snowflake:
    def __init__(self, worker_id: int, *, epoch_ms: int = _EPOCH_MS) -> None:
        if type(worker_id) is not int or not 0 <= worker_id <= _MAX_WORKER_ID:
            raise SnowflakeError("机器号无效")
        self._worker_id = worker_id
        self._epoch_ms = epoch_ms
        self._sequence = 0
        self._last_ms = -1
        self._lock = threading.Lock()

    def next_id(self) -> int:
        with self._lock:
            now = _now_ms()
            if now < self._last_ms:
                raise SnowflakeError("时钟回拨")
            if now == self._last_ms:
                self._sequence = (self._sequence + 1) & _MAX_SEQUENCE
                if self._sequence == 0:
                    now = _wait_next_ms(now)
            else:
                self._sequence = 0
            self._last_ms = now
            elapsed = now - self._epoch_ms
            if elapsed < 0:
                raise SnowflakeError("时钟早于起始时间")
            return (elapsed << _TIMESTAMP_SHIFT) | (self._worker_id << _WORKER_SHIFT) | self._sequence


def next_id() -> int:
    """取本进程的下一个订单号。"""
    return _shared().next_id()


def _shared() -> Snowflake:
    global _generator
    if _generator is not None:
        return _generator
    with _guard:
        if _generator is None:
            _generator = Snowflake(_worker_id())
        return _generator


def _worker_id() -> int:
    raw = os.environ.get("SNOWFLAKE_WORKER_ID", "0").strip()
    if not raw.isascii() or not raw.isdigit():
        raise SnowflakeError("机器号无效")
    return int(raw)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _wait_next_ms(last_ms: int) -> int:
    now = _now_ms()
    while now <= last_ms:
        time.sleep(0.0001)
        now = _now_ms()
    return now
