"""下单进入数据库之前的准入。

不存在的用户放进负缓存，同一编号在有效期内不再查库。
缓存里没有的用户查询先消耗令牌，令牌用完就直接拒绝。
写事务同时只放进少量请求，等待队列满了立即拒绝，不再占用数据库连接。
"""

import asyncio
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Lock

from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException

_CACHE_TTL_SECONDS = 60
_CACHE_MAX = 10_000
_BUCKET_CAPACITY = 20
_BUCKET_REFILL_PER_SECOND = 5
_WRITE_ACTIVE = 1
_WRITE_WAITING = 8


class _NegativeUsers:
    def __init__(self, ttl_seconds: float, max_size: int) -> None:
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._items: OrderedDict[int, float] = OrderedDict()
        self._lock = Lock()

    def missing(self, user_id: int) -> bool:
        now = time.monotonic()
        with self._lock:
            deadline = self._items.get(user_id)
            if deadline is None:
                return False
            if deadline <= now:
                del self._items[user_id]
                return False
            return True

    def remember(self, user_id: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._items[user_id] = now + self._ttl
            self._items.move_to_end(user_id)
            expired = [key for key, deadline in self._items.items() if deadline <= now]
            for key in expired:
                del self._items[key]
            while len(self._items) > self._max_size:
                self._items.popitem(last=False)


class _TokenBucket:
    def __init__(self, capacity: float, refill_per_second: float) -> None:
        self._capacity = capacity
        self._refill_per_second = refill_per_second
        self._tokens = capacity
        self._updated = time.monotonic()
        self._lock = Lock()

    def take(self) -> bool:
        now = time.monotonic()
        with self._lock:
            elapsed = now - self._updated
            self._updated = now
            self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_second)
            if self._tokens < 1:
                return False
            self._tokens -= 1
            return True


class _WriteQueue:
    def __init__(self, active_limit: int, wait_limit: int) -> None:
        self._active_limit = active_limit
        self._wait_limit = wait_limit
        self._active = 0
        self._waiting = 0
        self._condition = asyncio.Condition()

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._condition:
            if self._active >= self._active_limit and self._waiting >= self._wait_limit:
                raise BizException("请稍后再试", code=BizCode.CONFLICT)
            self._waiting += 1
            try:
                while self._active >= self._active_limit:
                    await self._condition.wait()
            except BaseException:
                self._waiting -= 1
                self._condition.notify()
                raise
            self._waiting -= 1
            self._active += 1
        try:
            yield
        finally:
            async with self._condition:
                self._active -= 1
                self._condition.notify()


_missing_users = _NegativeUsers(_CACHE_TTL_SECONDS, _CACHE_MAX)
_user_lookups = _TokenBucket(_BUCKET_CAPACITY, _BUCKET_REFILL_PER_SECOND)
_writes = _WriteQueue(_WRITE_ACTIVE, _WRITE_WAITING)


def prepare_user_lookup(user_id: int) -> None:
    """写库前调用。已知不存在或查询额度用完时，不打开数据库连接。"""
    if _missing_users.missing(user_id):
        raise BizException("用户不存在", code=BizCode.NOT_FOUND)
    if not _user_lookups.take():
        raise BizException("请稍后再试", code=BizCode.CONFLICT)


def remember_missing_user(user_id: int) -> None:
    """用户查询确认不存在后写入负缓存。"""
    _missing_users.remember(user_id)


def write_slot():
    """进入订单或充值的写事务。队列满时立即拒绝。"""
    return _writes.slot()
