"""数据库配置。

只保存连接参数。import 本模块或读取配置时都不创建、不连接数据库。
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from shopbot.framework.config.env import load_env


@dataclass(frozen=True)
class DatabaseConfig:
    path: Path
    echo: bool = False
    wal: bool = True
    busy_timeout_ms: int = 5000

    @property
    def url(self) -> str:
        return f"sqlite+aiosqlite:///{self.path.as_posix()}"


@lru_cache
def get_database_settings() -> DatabaseConfig:
    load_env()
    raw = os.environ.get("DATABASE_PATH", "").strip()
    if not raw:
        raise RuntimeError("缺少环境变量 DATABASE_PATH")
    return DatabaseConfig(path=Path(raw))
