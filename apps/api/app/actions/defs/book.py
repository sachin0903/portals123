"""The four-eye book actions on baton (Phase 3: folding reviews into gates).

Each wired review key (``app/reviews/catalogue.py``) becomes one apply leaf here. A review is an
action whose run holds on a run-level approval gate: the door captures the ``before`` snapshot at
propose time and starts the run with an ``Approval``; when an approver releases the gate, this leaf
runs. It re-reads the record, and if it no longer matches the snapshot it raises ``Permanent`` so
the run parks ``Conflicted`` rather than applying a stale change. With approval off the leaf runs
inside the door's budget, which is the old apply-immediately path.

The capture / apply / conflict logic is the one the live door already uses
(``app/reviews/apply.py``), so a held change and an immediate write cannot drift. This module only
registers the actions; the hold and decide paths move onto them in a later increment.
"""

from __future__ import annotations

from typing import Any

from baton import Permanent, RunContext, action

from app.reviews.apply import conflicts, handler
from app.reviews.catalogue import WIRED

_CONFLICT = "This record changed since the change was proposed, so it was not applied."


def _make_apply(key: str) -> Any:
    async def _apply(
        ctx: RunContext, *, payload: dict[str, Any], before: Any = None
    ) -> dict[str, Any]:
        wired = handler(key)
        current = await wired.capture_before(ctx.session, tenant_id=ctx.tenant_id, payload=payload)
        if conflicts(before, current):
            raise Permanent(_CONFLICT)
        try:
            await wired.apply(ctx.session, tenant_id=ctx.tenant_id, payload=payload)
        except (ValueError, LookupError) as exc:
            # A refused business rule (e.g. beneficiaries over 100%, a now-closed account): surface
            # the real reason as a permanent failure, rather than letting the generic handler park
            # it as a needs-attention crash with no message.
            raise Permanent(str(exc)) from exc
        return {"applied": True, "action_key": key}

    _apply.__name__ = f"apply_{key.replace('.', '_')}"
    return _apply


# One apply leaf per wired review key, registered onto the default registry on import. The firm
# catalogue for these stays in reviews/catalogue.py; the meta here is for baton's own listing.
for _spec in WIRED:
    action(
        key=_spec.key,
        name=_spec.label,
        description=_spec.description,
        subject_kinds=(_spec.subject_kind,),
        one_open_per_subject=_spec.one_open,
        on_failure="needs_attention",
        meta={
            "review": True,
            "family": _spec.family,
            "kind": "book",
            "event_type": _spec.event_type,
            "sensitivity": _spec.sensitivity,
            "reads": _spec.description,
        },
    )(_make_apply(_spec.key))
