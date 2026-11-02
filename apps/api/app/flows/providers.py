"""Identity / AML provider adapters — how a verify_identity / aml_check step gets automated.

A ``verify_identity`` step selects its adapter with ``config.provider``. ``manual`` is the default
and a genuine no-op: a staff member records the outcome in the case UI. An ``aml_check`` step has
no choice: it always runs on ComplyCube (``step_handlers.AmlCheckHandler``).

``complycube`` is wired for both step types (``app.complycube.provider``), and neither adapter calls
ComplyCube from the step. ``verify_identity`` asks the client for three photos
(``app/idv/capture.py``); their submit starts the check. ``aml_check`` queues its screen for the
worker (``app.complycube.screen``). Either way the step reports WAITING and
``app.complycube.routing.settle_portal_check`` releases it.

``creditsafe`` and ``idenfy`` are ALIASES of the ComplyCube adapters. Published flow versions are
immutable, so a journey already in flight still names them; its steps run on ComplyCube.
``ReviewHandler.preflight`` refuses both names on a new flow.

Registration is LAZY — read ``known()`` before changing it. Building the registry at import time
silently lost an adapter depending on module import order, and both resulting failures were
invisible.
"""

from __future__ import annotations

import importlib
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class ReviewProvider(Protocol):
    """A verify_identity / aml_check automation. ``manual`` (the default) is a no-op — a human
    enters the outcome. A real provider starts a check and reports via ``complete_review``.

    ``session`` and ``kyc_check_id`` are keyword-only and optional so ``manual`` keeps ignoring
    them. A real provider needs both: the session to enqueue its work in the SAME transaction as
    the step transition (so a requested check and a committed step cannot diverge), and the check id
    to report the outcome against later.
    """

    name: str

    async def start(
        self,
        *,
        tenant_id: int,
        case_id: int,
        step_id: str,
        subject_contact_id: int | None,
        session: object = None,
        kyc_check_id: int | None = None,
    ) -> None: ...


class ManualProvider:
    """The default: no external call — a staff member reviews and records the outcome in the UI."""

    name = "manual"

    async def start(self, **_: object) -> None:  # noqa: D102 - Protocol-satisfying no-op
        return None


_MANUAL = ManualProvider()

COMPLYCUBE = "complycube"
IDENTITY_STEP = "verify_identity"
AML_STEP = "aml_check"
#: Provider names a published version may still carry. They resolve to the ComplyCube adapters, and
#: a NEW flow may not name them (``ReviewHandler.preflight``).
RETIRED: frozenset[str] = frozenset({"creditsafe", "idenfy"})

#: Resolved adapters, memoised once they import cleanly. Deliberately NOT built at module import:
#: ``app.complycube.provider`` imports the flows outbox, so `app.complycube.provider` → `app.flows`
#: → this module → `app.complycube.provider` is a real cycle. Resolving on FIRST USE rather than at
#: import time is what makes the cycle harmless — by the time a step actually runs, both modules
#: are fully initialised.
_resolved: dict[tuple[str, str], ReviewProvider] = {}

#: The optional adapters and where they live, keyed by ``(provider name, step type)``: one name
#: serves both step types with one adapter per type. The retired names are aliases (module header).
_OPTIONAL: dict[tuple[str, str], tuple[str, str]] = {
    (COMPLYCUBE, IDENTITY_STEP): ("app.complycube.provider", "ComplyCubeIdentityProvider"),
    (COMPLYCUBE, AML_STEP): ("app.complycube.provider", "ComplyCubeAmlProvider"),
    ("idenfy", IDENTITY_STEP): ("app.complycube.provider", "ComplyCubeIdentityProvider"),
    ("creditsafe", AML_STEP): ("app.complycube.provider", "ComplyCubeAmlProvider"),
}


def known() -> dict[tuple[str, str], ReviewProvider]:
    """Every adapter that can actually run here, keyed by ``(name, step type)``, resolved on demand.

    This function exists because the obvious version of it was a silent landmine. Building the
    registry at module import and swallowing the ImportError meant that if ANY module imported an
    adapter module before ``app.flows`` — which ruff's own alphabetical import ordering does — the
    partially-initialised module satisfied ``sys.modules`` without yet defining the class, the error
    was discarded, and the adapter was missing from the registry for the entire process life. Two
    silent failures followed: ``get_provider`` handed back ManualProvider, so no work was ever
    raised and the step waited on a human nobody had told; and ``preflight`` rejected the same
    config as an unknown provider, blocking publish. Neither logged anything.

    So: resolve lazily, memoise only on success, and never swallow the reason.
    """
    for key, (module_path, attr) in _OPTIONAL.items():
        if key in _resolved:
            continue
        try:
            module = importlib.import_module(module_path)
            _resolved[key] = getattr(module, attr)()
        except Exception:  # noqa: BLE001 - a missing adapter must not take the flows engine down
            # Not `pass`: an adapter that cannot load is the difference between an automated check
            # and a step that waits forever, so it has to be visible.
            logger.exception(
                "flows: review provider %r for %s could not be loaded from %s — steps configured "
                "for it will fall back to manual review",
                key[0],
                key[1],
                module_path,
            )
    return dict(_resolved)


def names(step_type: str | None = None) -> set[str]:
    """The provider names that can run here (for ``step_type`` when given), ``manual`` included."""
    return {"manual"} | {name for name, kind in known() if step_type is None or kind == step_type}


def get_provider(name: str | None, step_type: str | None = None) -> ReviewProvider:
    """The adapter for a step's ``config.provider`` (falls back to manual, staff-review).

    ``step_type`` picks between a name's adapters; without it the name's first registered adapter
    is returned.
    """
    if not name or name == "manual":
        return _MANUAL
    resolved = known()
    if step_type is not None:
        return resolved.get((name, step_type), _MANUAL)
    return next((adapter for (n, _kind), adapter in resolved.items() if n == name), _MANUAL)
