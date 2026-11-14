"""管理员身份缓存。命中后不再查库；角色或停用状态变化时立刻失效。"""

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

from sqlalchemy import event
from sqlalchemy.orm.attributes import get_history

from shopbot.framework.enums.model import UserRole
from shopbot.shop.models import User

_MAX = 1024


@dataclass(frozen=True)
class AdminIdentity:
    user_id: int
    telegram_id: int
    role: int


class _AdminCache:
    def __init__(self) -> None:
        self._items: OrderedDict[int, AdminIdentity] = OrderedDict()
        self._generations: dict[int, int] = {}
        self._lock = Lock()

    def lookup(self, telegram_id: int) -> tuple[int, AdminIdentity | None]:
        with self._lock:
            return self._generations.get(telegram_id, 0), self._items.get(telegram_id)

    def remember(self, telegram_id: int, generation: int, identity: AdminIdentity) -> None:
        """只接受查库开始时的世代。期间若已失效，这次结果不写入。"""
        with self._lock:
            if self._generations.get(telegram_id, 0) != generation:
                return
            self._items[telegram_id] = identity
            self._items.move_to_end(telegram_id)
            while len(self._items) > _MAX:
                self._items.popitem(last=False)

    def forget(self, telegram_id: int) -> None:
        with self._lock:
            self._generations[telegram_id] = self._generations.get(telegram_id, 0) + 1
            self._items.pop(telegram_id, None)


_admins = _AdminCache()


def lookup_admin(telegram_id: int) -> tuple[int, AdminIdentity | None]:
    return _admins.lookup(telegram_id)


def remember_admin(telegram_id: int, generation: int, identity: AdminIdentity) -> None:
    _admins.remember(telegram_id, generation, identity)


def forget_admin(telegram_id: int) -> None:
    _admins.forget(telegram_id)


def _forget_changed(target: User) -> None:
    role_changed = get_history(target, "role").has_changes()
    active_changed = get_history(target, "is_active").has_changes()
    identity_changed = get_history(target, "telegram_id").has_changes()
    if not role_changed and not active_changed and not identity_changed:
        return
    for previous in get_history(target, "telegram_id").deleted:
        forget_admin(previous)
    forget_admin(target.telegram_id)


@event.listens_for(User, "after_update")
def _admin_cache_after_update(_mapper: object, _connection: object, target: User) -> None:
    _forget_changed(target)


@event.listens_for(User, "after_delete")
def _admin_cache_after_delete(_mapper: object, _connection: object, target: User) -> None:
    if target.role == UserRole.ADMIN:
        forget_admin(target.telegram_id)
