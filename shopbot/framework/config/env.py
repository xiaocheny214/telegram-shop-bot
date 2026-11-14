"""启动时读取项目根目录的 .env。已经存在的环境变量不覆盖。"""

import os
from pathlib import Path

_loaded = False
_ROOT = Path(__file__).resolve().parents[3]


def load_env() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    seen: set[Path] = set()
    for path in (_ROOT / ".env", Path.cwd() / ".env"):
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        _apply(resolved)


def _apply(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not name or name in os.environ:
            continue
        os.environ[name] = _unescape(value.strip())


def _unescape(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return value.replace("\\n", "\n")
