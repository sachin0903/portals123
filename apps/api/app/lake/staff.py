"""A custodian's own employee directory, from the firm's own slice.

**The option list for mapping a Graphene login to the custodian employee it is** — the manual half
of the adviser↔custodian mapping, for the people the automatic half could not match.

`auth.merge_provider_user` (0123) attaches `auth.users.provider_refs` wherever the custodian's
address matches a login's exactly. That is the common case and it needs nobody. What it leaves is
the ordinary one it must not guess at: a firm's SEI address and its Graphene login address are
frequently different, and the merge skips rather than inventing a link. This is where an operator
finishes the job.

---------------------------------------------------------------------------
WHY THE LAKE AND NOT A LIVE CALL, which reverses what 0118 planned.
---------------------------------------------------------------------------

0118 argued a custodian's people need no storing because "the option list is read live from the
provider — SEI's is `firmemployeeusers`". That was right when nothing held them. It is wrong now,
for three reasons, and the third is the one that decides it:

1. **The data is already here.** `services/sei` fetches exactly that endpoint nightly into
   `ref.employee_users`, conforms it with the drop into `platform.users`, and the firm's chain
   projects it to `tenant_{uid}.users`. A live call would fetch a second copy of last night's
   answer.
2. **It is CONFORMED here and raw there.** The lake relation is already scoped to the firm, already
   filtered to `USER_TYPE = 1` (the drop's 1,591 rows are 706 employees, 865 END CLIENTS and 20
   service logins), and already carries the split name. A live response carries none of that —
   it has no `userType` field at all — so a picker fed from it would offer a firm its own clients
   as advisers.
3. **SEI ALLOWLISTS ITS API BY SOURCE IP.** The pipeline runs where that allowlist points; the API
   process need not, and a picker that 403s from one deployment and works from another is a
   feature that appears broken rather than unavailable. Reading the lake works wherever the chain
   has run, and says so plainly when it has not.

`pkgs/sei`'s own `ReferenceApi.firm_employee_users` therefore has no caller in the application. It
stays as part of that client's faithful surface over the vendor's API — the same standing as every
other endpoint there without an in-app consumer — and this module's existence is the reason it does
not gain one.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.lake import core as lake_core
from app.lake.holdings import HoldingsUnavailable, guarded, read_slice, require_uid

# ---- THE PICKER IS TYPED INTO, so the slice is cached and the search runs in Python -------------
#
# This is the one interactive reader of the lake in the estate: `apps/platform`'s
# `/team/provider-staff` endpoint forwards a `?q=` from a keystroke, where `firm_holdings` and
# `firm_value_series` are once-per-page loads. DuckDB connections are not thread-safe and these
# reads run on worker threads; the core pool's checkout is what keeps one connection off two
# threads. Sharing one concurrently would trade a cheap attach for a bug that only appears
# under load.
#
# The core-catalogue pool lends an already-attached connection to one thread at a time, so a
# cache miss no longer pays INSTALL and ATTACH. This cache stays, because a keystroke should
# not take a connection at all while the directory is warm. The relation is refreshed NIGHTLY
# by the provider's chain, and a firm's employee directory is small (706 rows in the whole
# p04 corpus, 546 of them for its largest firm). A keystroke costs a substring match over a
# list already in memory.
#
# `_SLICE_CAP` is the read's own bound rather than the picker's: it is what keeps a pathological
# firm from pinning an unbounded list in every replica, and it is far above any real directory.
# Only a SUCCESSFUL read is cached — an unavailable chain must keep saying so, because "this
# custodian has told us about nobody" and "we cannot read what it told us" need different answers.
_SLICE_TTL_S = 60.0
_SLICE_CAP = 5_000
_slice_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_slice_locks: dict[tuple[str, str], asyncio.Lock] = {}

#: The columns a picker needs, and no more. Deliberately NOT `data`: the provider's whole payload
#: is on the relation and none of it belongs in an option list, where every extra field is one more
#: thing rendered into a dropdown a person is scanning quickly.
_STAFF_SQL = """
    SELECT external_id,
           email,
           display_name,
           first_name,
           last_name,
           login_name,
           is_active
      FROM {relation}
     -- ACTIVE FIRST, then by name. 470 of the p04 corpus's 706 employees are already departed at
     -- SEI, and they are published on purpose (a departed adviser is still the name on every
     -- account they advised) — but somebody mapping a colleague wants the current ones first.
     -- Ordered HERE rather than after the search, so the cached slice is already in picker order.
     ORDER BY is_active DESC NULLS LAST,
              coalesce(display_name, email, external_id)
     LIMIT $1
"""


def _matches(row: dict[str, Any], needle: str) -> bool:
    """The `LIKE '%…%'` the query used to do, over the same three fields."""
    haystack = " ".join(
        str(row.get(field) or "") for field in ("display_name", "email", "login_name")
    ).lower()
    return needle in haystack


async def _slice(provider: str, tenant_uid: str) -> list[dict[str, Any]]:
    """The firm's whole employee directory, cached for `_SLICE_TTL_S` and single-flighted.

    The lock collapses concurrent misses in one API process, which is what a picker produces:
    three characters typed quickly are three requests, and without it each one builds its own
    DuckDB stack against the same unchanged relation.
    """
    key = (provider, tenant_uid)
    cached = _slice_cache.get(key)
    if cached and time.monotonic() - cached[0] < _SLICE_TTL_S:
        return cached[1]
    lock = _slice_locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _slice_cache.get(key)
        if cached and time.monotonic() - cached[0] < _SLICE_TTL_S:
            return cached[1]
        rows = await guarded(
            provider,
            asyncio.to_thread(
                read_slice,
                provider,
                tenant_uid,
                _STAFF_SQL,
                [_SLICE_CAP],
                statement="users",
                relation="users",
            ),
        )
        _slice_cache[key] = (time.monotonic(), rows)
        return rows


def forget_staff(provider: str | None = None) -> None:
    """Drop the cached slices — for a chain that has just re-run, and for tests."""
    for key in [k for k in _slice_cache if provider is None or k[0] == provider]:
        del _slice_cache[key]
        _slice_locks.pop(key, None)


async def firm_staff(
    provider: str, tenant_uid: str, *, search: str = "", limit: int = 50
) -> list[dict[str, Any]]:
    """The custodian's employees for this firm, active first then by name.

    Raises `HoldingsUnavailable` when the firm's chain has not run, rather than returning `[]`.
    That distinction is the whole reason the lake readers raise: "this custodian has told us about
    nobody" and "we cannot read what it told us" need different answers from a picker, and an empty
    dropdown that means the second is how somebody concludes their firm has no staff at SEI.
    """
    require_uid(tenant_uid)
    rows = await _slice(provider, tenant_uid)
    needle = search.strip().lower()
    matched = rows if not needle else [row for row in rows if _matches(row, needle)]
    return matched[: max(1, min(limit, 200))]


__all__ = ["HoldingsUnavailable", "firm_staff", "forget_staff", "lake_core"]
