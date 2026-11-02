"""The action table. One registry per process; the app registers its ``@action`` definitions
onto the default, or passes its own. The built-in ``baton.gate`` lives here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from baton.errors import BatonError

if TYPE_CHECKING:
    from baton.definitions import ActionDefinition

GATE_KEY = "baton.gate"


class RegistryError(BatonError):
    pass


class Registry:
    def __init__(self) -> None:
        self._by_key: dict[str, ActionDefinition[Any]] = {}

    def register(self, defn: ActionDefinition[Any]) -> None:
        existing = self._by_key.get(defn.key)
        if existing is not None and existing is not defn:
            raise RegistryError(f"action {defn.key!r} is already registered")
        self._by_key[defn.key] = defn

    def get(self, key: str) -> ActionDefinition[Any] | None:
        return self._by_key.get(key)

    def require(self, key: str) -> ActionDefinition[Any]:
        defn = self._by_key.get(key)
        if defn is None:
            raise RegistryError(f"no action registered for {key!r}")
        return defn

    def known(self, key: str) -> bool:
        return key in self._by_key

    def all(self) -> list[ActionDefinition[Any]]:
        return list(self._by_key.values())


default_registry = Registry()
