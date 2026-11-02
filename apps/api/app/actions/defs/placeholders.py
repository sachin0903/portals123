"""Catalogue actions with no implementation in either engine, registered on baton as actions that
refuse. This keeps the catalogue complete on baton (the generic Run button reaches them with a
clear "not implemented yet") and lets the legacy engine be removed. Each is a real feature to build
later; the catalogue spec (name, providers, subject) is the single source for how it is listed.
"""

from __future__ import annotations

from typing import Any

from baton import Permanent, RunContext, action

from app.actions.catalogue import get as _catalogue_action

_PLACEHOLDERS = ("beneficiary.replace", "drawdown.start", "trade.place", "illustration.generate")


def _make(name: str) -> Any:
    async def _run(_ctx: RunContext, **_params: Any) -> dict[str, Any]:
        # No provider API yet. A firm that sets this action to MANUAL never reaches here: the
        # engine completes a manual run at gate-release (engine.decide) and never schedules the
        # body. So the body runs only when the action is NOT manual, and then it can only refuse.
        raise Permanent(f"{name} is not implemented yet.")

    return _run


for _key in _PLACEHOLDERS:
    _spec = _catalogue_action(_key)
    if _spec is None:  # pragma: no cover - the catalogue is the source of these keys
        continue
    action(
        key=_spec.key,
        name=_spec.name,
        description=_spec.description,
        subject_kinds=_spec.subject_kinds,
        meta={
            "catalogue": True,
            "category": _spec.category,
            "kind": "todo",
            "providers": list(_spec.providers),
            "event_type": _spec.event_type,
            "reads": _spec.description,
        },
    )(_make(_spec.name))
