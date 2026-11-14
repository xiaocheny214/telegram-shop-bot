"""一次调用上的审计上下文。路径和 IP 由 HTTP 会话放进来，服务函数只读取。"""

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass
class AuditContext:
    path: str | None = None
    client_ip: str | None = None
    operator_user_id: int | None = None
    operator_telegram_id: int | None = None


_context: ContextVar[AuditContext | None] = ContextVar("audit_context", default=None)


def bind_request(path: str, client_ip: str | None) -> Token[AuditContext | None]:
    return _context.set(AuditContext(path=path, client_ip=client_ip))


def reset_request(token: Token[AuditContext | None]) -> None:
    _context.reset(token)


def current_context() -> AuditContext:
    context = _context.get()
    if context is None:
        context = AuditContext()
        _context.set(context)
    return context


def set_operator(*, user_id: int | None = None, telegram_id: int | None = None) -> None:
    context = current_context()
    context.operator_user_id = user_id
    context.operator_telegram_id = telegram_id
