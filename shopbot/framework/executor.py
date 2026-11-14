"""支付渠道的阻塞调用使用的线程池。数据库写入不进这个池。"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

_WORKERS = 4
_pool: ThreadPoolExecutor | None = None
T = TypeVar("T")


def start_executor(workers: int = _WORKERS) -> None:
    """应用启动时创建。重复调用沿用已经存在的池。"""
    global _pool
    if _pool is not None:
        return
    _pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="shop-payment")


def stop_executor() -> None:
    """应用关闭时停掉。正在执行的渠道请求不会再排队。"""
    global _pool
    pool = _pool
    _pool = None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


async def run_blocking(func: Callable[..., T], /, *args: object, **kwargs: object) -> T:
    """把同步函数丢进支付线程池。池还没启动时按默认大小创建。"""
    import asyncio
    from functools import partial

    if _pool is None:
        start_executor()
    assert _pool is not None
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_pool, partial(func, *args, **kwargs))
