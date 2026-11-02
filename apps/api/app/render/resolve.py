"""Render-time dataset resolution — turn a template's linked dataset *codes* into row lists that
charts, tables and Jinja ``{% for %}`` loops consume at generation time (docs/document-generation.md
§6).

A dataset is keyed by its CODE, and for each code the latest **published** version is materialised:

* ``catalogue`` → a Graphene-authored parameterised query (``app/render/catalogue.py``), compiled
  with the firm's chosen settings and run under the envelope below
* ``inline``    → the stored ``inline_data`` jsonb (``{columns, rows}``, or a bare list of
  row-objects)

Rows are returned as ``list[dict]`` keyed by column name.

A code that names a SHIPPED CATALOGUE ENTRY resolves with **no database row at all**: entries live
in code, so ``_dataset_source`` falls back to the registry and every firm can use a new entry the
moment it ships. A firm that configures one gets a row of its own carrying ``catalogue_ref``
(the entry key plus its parameters), which is a separate dataset beside the shipped one rather than
a shadow of it.

FREE-TEXT SQL AUTHORING IS GONE (0116), and with it ~370 lines: a keyword denylist, a function
denylist, a schema-prefix denylist, a hand-written lexer and a bind allowlist, all guarding a query
no reviewer had seen. The replacement is not a better validator but a smaller question — a firm
chooses an entry and its settings, and a setting is a VALUE bound by the driver, never text spliced
into SQL. No parameter names a table, a column, an ordering or a predicate, so the SQL of an entry
is a constant and there is nothing left to validate.

What did NOT go is the runtime envelope, which is now the whole of the defence and applies to every
dataset query through one function (:func:`_execute_scoped`): ``SET LOCAL ROLE authenticated`` so
RLS applies inside a BYPASSRLS session, claims minted to match the dataset's scope,
``transaction_read_only``, a statement timeout, a database-enforced row cap, a SAVEPOINT rolled
back afterwards, and a **post-statement principal re-check** that discards the rows if the
statement moved the role or ``request.jwt.claims`` while it ran. That layer mattered most once the
denylist went: it depends on nothing having named every spelling of ``set_config``, so a rewrite
reached through a view's function or a custom operator is caught there or nowhere.

**Failures are reported, not swallowed.** ``resolve_datasets_reporting`` returns the rows *and* a
``DatasetProblem`` per code that could not be resolved (missing, disabled, unpublished, an execution
error, a truncated result, an empty result), so the caller can fail the render loudly or record the
problem on the document instead of shipping a plausible-looking client PDF with empty charts.
``resolve_datasets`` keeps the older rows-only contract for callers that don't consume problems yet
(it logs them).

``scope`` decides **who the query runs as** at render:

* ``subject`` — the rows belong to one client. A background render resolves it under claims scoped
  to that contact AND that contact's own accounts. **The entry's own WHERE clause is the scoping
  mechanism, not RLS** — measured on the live schema, ``platform.valuations`` has no ``contact_id``
  column and so no contact branch in its policy (returning ZERO rows for a per-client query), while
  ``platform.contributions``' tenant branch is not gated on ``auth.can_read_all()`` (returning the
  WHOLE FIRM). The policy set disagrees with itself across these tables, so a per-client entry
  filters on ``:subject_id`` itself and RLS is the backstop; ``catalogue.Entry`` refuses to declare
  one that does not.
* ``global`` — the rows are the same for every client (the firm's charge schedule). A background
  render resolves it under claims that can see the whole firm (``contacts:read_all`` for the
  dataset's own tenant, and nothing else: never another tenant, never a write). Before this, a
  firm-wide dataset was silently narrowed to the document's subject contact, so
  ``SELECT count(*) FROM crm.contacts`` in a client's PDF quietly read ``1``.

When a ``global`` dataset is resolved under claims that CANNOT see the firm (a preview running as
the caller, or an explicitly supplied principal), the shortfall is reported — advisory for the
caller's own preview, blocking for a background resolve — instead of returning a subset that reads
like the whole answer.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.permissions import CONTACTS_READ_ALL
from app.render import catalogue

logger = logging.getLogger("document_worker")

# ── dataset execution limits ──────────────────────────────────────────────────
# Every dataset query runs with a hard budget, ours included: without one a query whose plan blows
# up on a large book pins a shared render worker until the job's visibility window expires, at which
# point the job is reclaimed and re-run — a self-amplifying denial of service.
DATASET_ROW_LIMIT = 5_000
DATASET_TIMEOUT_MS = 15_000

# The one bind a dataset query may carry: the render's subject client (crm.contacts.id).
SUBJECT_BIND = "subject_id"

# A dataset value can arrive as a currency/percent string ("£20,000", "5.0%"); charts need numbers.
_NUM_STRIP = re.compile(r"[^0-9.\-]")


class DatasetSqlError(ValueError):
    """A dataset query whose ROWS must not be returned: it moved the session's identity while it
    ran, so the rows were scanned under an identity the query chose for itself.

    Raised by :func:`_execute_scoped`'s post-statement re-check and by nothing else. The message is
    author-facing (it reaches the authoring form and the render's problem list)."""


# Problem reasons that describe the data rather than a fault: the rows are genuine (or the name was
# never a dataset in the first place), so they are reported but must never fail a render.
# ``preview_scope`` is the caller-facing half of the scope check: on someone's own screen, "your
# clients, not the firm's" is the correct answer — it just has to be said out loud. The BLOCKING
# half is ``scope_narrowed`` (a background resolve that would put a subset in a document),
# deliberately NOT in this set.
_ADVISORY = frozenset({"empty", "truncated", "not_a_dataset", "preview_scope"})


@dataclass(frozen=True, slots=True)
class DatasetProblem:
    """One dataset code that did not resolve to trustworthy rows.

    ``reason`` is a stable machine token; ``detail`` is a safe, author-facing sentence (never a raw
    driver error — a dataset query can carry the firm's SQL and, in an exception string, its data).
    """

    code: str
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class DatasetResolution:
    """Resolved rows per code, plus every problem hit on the way."""

    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    problems: list[DatasetProblem] = field(default_factory=list)

    @property
    def blocking(self) -> list[DatasetProblem]:
        """Problems that mean a document would be built on data that is missing or wrong — the set
        worth failing a render over. ``empty`` and ``truncated`` are advisory: the rows returned are
        genuine, there are just none of them, or not all of them. ``not_a_dataset`` is advisory too:
        a bare loop source that isn't a dataset is normally a context variable, not a fault. So is
        ``preview_scope``: a caller previewing a firm-wide dataset sees their own book, which is
        right for a preview and wrong only if mistaken for the document."""
        return [p for p in self.problems if p.reason not in _ADVISORY]

    def message(self) -> str | None:
        """Every problem as one author-facing sentence — what a caller records on the render job's
        error, notes on the document or shows in a toast. ``None`` when everything resolved.

        Kept here so a consumer (the worker, a router) needs one line rather than its own
        formatting: ``if resolution.blocking: raise RuntimeError(resolution.message())``."""
        return " ".join(p.detail for p in self.problems) or None


def to_number(value: Any) -> float | None:
    """Best-effort numeric coercion for chart values. Strips currency/percent/thousands separators;
    returns None when there's no number to read (so a chart skips the point rather than crash)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    cleaned = _NUM_STRIP.sub("", str(value)).strip()
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def rows_from_static(static_data: Any) -> list[dict[str, Any]]:
    """Normalise a static version's ``static_data`` into ``list[dict]``. Accepts the stored
    ``{"columns": [...], "rows": [[...]]}`` shape or a bare list of row-objects."""
    if isinstance(static_data, dict):
        cols = static_data.get("columns")
        rows = static_data.get("rows")
        if isinstance(cols, list) and isinstance(rows, list):
            names = [str(c) for c in cols]
            return [
                {name: row[i] if i < len(row) else None for i, name in enumerate(names)}
                for row in rows
                if isinstance(row, list)
            ]
    if isinstance(static_data, list):
        return [dict(r) for r in static_data if isinstance(r, dict)]
    return []


def first_rows(datasets: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """First row of each dataset as a dict, so a SINGLE value binds as ``{{ one.<code>.<col> }}``
    without a ``{% for %}`` loop — the common case for a subject-scoped dataset (one row per
    client). Empty dict when a dataset yielded no rows, so the token renders "" rather than
    erroring. Worker + preview both build ``one`` from this, so the namespace can't diverge."""
    return {code: (rows[0] if rows else {}) for code, rows in datasets.items()}


# ── source lookup ─────────────────────────────────────────────────────────────
async def _dataset_source(
    session: AsyncSession, tenant_id: int, code: str
) -> dict[str, Any] | None:
    """The dataset row + its latest PUBLISHED version's source, or None when the code is unknown to
    this tenant. Disabled datasets and datasets with no published version are returned (with
    ``enabled`` / ``published`` telling the caller why there are no rows) so resolution can report
    *which* problem it hit instead of returning a bare ``[]``.

    Only published versions resolve: a draft is unfinished authoring and must never reach a client
    document (the old "published, else latest" fallback could ship one)."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT d.enabled, d.format::text AS format, d.scope::text AS scope, "
                    "       v.id IS NOT NULL AS published, v.inline_data, "
                    "       v.catalogue_ref ->> 'key' AS catalogue_key, "
                    "       v.catalogue_ref -> 'params' AS catalogue_params, "
                    "       EXISTS (SELECT 1 FROM documents.dataset_versions av "
                    "               WHERE av.dataset_id = d.id) AS has_versions "
                    "FROM documents.datasets d "
                    "LEFT JOIN LATERAL ("
                    "  SELECT v.id, v.inline_data, v.catalogue_ref "
                    "  FROM documents.dataset_versions v "
                    "  WHERE v.dataset_id = d.id AND v.status = 'published' "
                    "  ORDER BY v.version_number DESC LIMIT 1"
                    ") v ON true "
                    "WHERE d.tenant_id = :t AND d.code = :code"
                ),
                {"t": tenant_id, "code": code},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row:
        return dict(row)
    # No row of this firm's own — but the code may name a CATALOGUE entry, which is code-side and
    # has no row until a firm configures one (``app/render/catalogue.py``). A shipped entry is
    # therefore usable by every firm immediately, and a configured row above simply overrides it.
    entry = catalogue.by_key(code)
    if entry is None:
        return None
    return {
        "enabled": True,
        "format": "catalogue",
        "scope": entry.scope,
        "published": True,
        "inline_data": None,
        "has_versions": True,
        # The entry itself, so the reporting loop does not look it up a second time and cannot
        # disagree with this one about which entry a code names.
        "catalogue_key": entry.key,
        "catalogue_params": None,
    }


async def _session_principal(session: AsyncSession) -> tuple[str, str | None]:
    """The session's current DB role and published claims — how we tell a real request principal
    (``authenticated`` + claims, i.e. a caller whose own scope must apply) from a privileged
    background session (``service_role``, BYPASSRLS, as in the render worker)."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT current_role AS role, "
                    "       current_setting('request.jwt.claims', true) AS claims"
                )
            )
        )
        .mappings()
        .one()
    )
    return str(row["role"]), row["claims"] or None


async def _subject_account_ids(session: AsyncSession, tenant_id: int, subject_id: Any) -> list[int]:
    """The accounts belonging to the render's own client, for the minted claims' account scope.

    Minted subject claims used to carry NO accounts at all, which made every account-scoped table
    unreadable. Measured on the live schema: ``platform.valuations``' only client-side policy
    branch is ``account_id = ANY (auth.account_scope())`` — there is no ``contact_id`` column to
    have a branch — so a per-client valuations query returned zero rows, which in a document is
    indistinguishable from a client who holds nothing. Declaring such an entry ``global`` is not
    the way out either: ``resolve_datasets_reporting`` refuses ``global`` + ``:subject_id``.

    So this mints what a real client-portal principal would carry for that person, and RLS then
    narrows to the same set it would for them rather than to the empty set. It runs BEFORE the
    downgrade, on the ambient (``service_role``) session, and is scoped by tenant AND contact in
    its own WHERE — the claims it feeds are the thing being established, so it cannot lean on them.
    """
    if not isinstance(subject_id, int):
        return []
    rows = await session.execute(
        text("SELECT id FROM platform.accounts WHERE tenant_id = :t AND contact_id = :c"),
        {"t": tenant_id, "c": subject_id},
    )
    return [int(r[0]) for r in rows]


def _minted_claims(
    tenant_id: int, subject_id: Any, scope: str, account_ids: list[int] | None = None
) -> dict[str, Any]:
    """Claims for a dataset query with **no request principal** (the render worker's
    ``service_role`` session), minted to mean exactly what the dataset's ``scope`` declares:

    * ``subject`` — no permissions at all, and just the render's own client and that client's own
      accounts in scope. A per-client dataset resolves to that client and nothing else. The
      accounts matter: several tables have no ``contact_id`` to scope by and their only client-side
      policy branch is ``auth.account_scope()`` (see :func:`_subject_account_ids`).
    * ``global`` — ``contacts:read_all`` for the dataset's OWN tenant and no contact scope, i.e.
      firm-wide within one firm. This is what makes ``scope='global'`` honest: without it a
      firm-wide dataset was narrowed to the document's subject contact, so it silently produced a
      subset (one row, or a count of 1) in a client's PDF.

    What it deliberately is NOT: this is not a general widening. ``contacts:read_all`` is the only
    permission any RLS policy consults, so a ``global`` render sees the firm's rows and gains
    nothing else — no other tenant (``tenant_ids`` is this one firm), no ``system`` bypass (``role``
    is ``service``, which ``auth.is_system()`` does not honour), and no writes (it runs in a
    read-only transaction). Declaring a ``global`` SQL dataset in the first place is gated on
    ``contacts:read_all`` at authoring time, so this cannot be an author's route to the firm's book.

    ``subject_id`` is ignored for a ``global`` dataset: such a query may not bind ``:subject_id`` at
    all (the API and ``resolve_datasets_reporting`` both refuse the combination).
    """
    firm_wide = scope != "subject"
    return {
        "sub": "render-worker",
        "role": "service",
        "permissions": [CONTACTS_READ_ALL.slug] if firm_wide else [],
        "portal": "platform",
        "tenant_id": tenant_id,
        "tenant_ids": [tenant_id],
        "user_uuid": None,
        "contact_ids": [] if firm_wide else ([subject_id] if isinstance(subject_id, int) else []),
        # Empty for a firm-wide render: `contacts:read_all` already opens the tenant branch of every
        # policy, so naming accounts would add nothing and would be a second, narrower claim to keep
        # in step.
        "account_ids": [] if firm_wide else list(account_ids or []),
    }


def _wrap_with_cap(stmt: str, row_limit: int = DATASET_ROW_LIMIT) -> str:
    """Wrap a statement so the database, not the worker's memory, enforces the row cap.
    Fetches one row beyond the cap so truncation can be *reported* rather than silently applied.
    The newlines matter: they terminate any trailing line comment in the statement."""
    # noqa S608: `stmt` is a catalogue entry's own constant SQL — declared in
    # `app/render/catalogue.py` or `app/reports/catalogue.py`, never author-supplied — and the
    # only interpolated number is the caller's cap. The wrapper is the row cap, not a query builder.
    cap = int(row_limit) + 1
    return f"SELECT * FROM (\n{stmt}\n) AS dataset_rows\nLIMIT {cap}"  # noqa: S608


@dataclass(frozen=True, slots=True)
class _SqlRun:
    """One SQL dataset execution: the rows, plus what the caller needs to judge them."""

    rows: list[dict[str, Any]]
    truncated: bool
    # A ``global`` (firm-wide) dataset that ran under claims which cannot see the whole firm, so its
    # rows are a SUBSET of what it declares. Always False for a ``subject`` dataset (there is no
    # firm-wide claim to be short of) and for minted claims (they are minted to match the scope).
    narrowed: bool = False
    # True when it ran under the session's own AMBIENT claims — a caller looking at their own
    # screen, for whom "your scope, not the firm's" is correct behaviour rather than a fault.
    borrowed: bool = False


class DatasetBindMismatch(RuntimeError):
    """The binds handed to a dataset query are not the ones its SQL names.

    Ours to fix, never the firm's — which is why it is not a :class:`DatasetSqlError`. It exists
    because SQLAlchemy's bind scanner reads ``:from_date::date`` as a bind named ``from_dat`` and
    ``:a::text, :b`` as the single bind ``b``: it truncates or drops silently, and Postgres then
    answers with a bare syntax error. Without this check that error reaches the author as "could
    not be read. Check the query and try again." about a query we wrote and they cannot see.
    """


async def _execute_scoped(
    session: AsyncSession,
    stmt: str,
    params: dict[str, Any],
    *,
    tenant_id: int,
    subject_id: Any,
    scope: str,
    claims: dict[str, Any] | None = None,
    row_limit: int = DATASET_ROW_LIMIT,
    timeout_ms: int = DATASET_TIMEOUT_MS,
) -> _SqlRun:
    """Run one dataset statement under the full envelope, and judge the identity it ran as.

    THE ONLY PLACE a dataset query is executed. Both callers — a catalogue entry's compiled SQL and
    a legacy stored SELECT — share this body rather than each arranging their own downgrade, so
    they physically cannot differ on the role, the claims, the savepoint, the read-only flag, the
    timeout, the row cap or the post-statement re-check. A flag on one function would have been the
    shape someone later flips on the other path.

    Safety, in layers:

    * the caller's OWN scope — a session that already carries claims keeps them, so a book-scoped
      adviser resolving a dataset sees exactly what they may see, and no dataset can widen an
      existing principal. Only a session with NO published claims (the ``service_role`` render
      worker) gets minted ones, and those say exactly what ``scope`` declares (``_minted_claims``:
      this client for ``subject``, this firm for ``global``); a caller can pass ``claims=`` to run
      as a specific principal.
    * ``scope`` integrity — a ``global`` dataset resolved under claims that cannot see the firm
      returns a subset, which is the silent-wrongness this whole module exists to stop. It is
      detected here (``SELECT auth.can_read_all()`` under the effective claims, so the check is the
      database's own definition rather than a re-implementation) and reported through ``narrowed``.
    * ``SET LOCAL ROLE authenticated`` — RLS applies even inside a BYPASSRLS session.
    * ``transaction_read_only`` — a write anywhere in the statement (including inside a function)
      fails, and ``statement_timeout`` bounds the runaway case.
    * a **principal-integrity backstop** — the role and claims are re-read after the statement and
      the rows are discarded if either moved. This is the layer that depends on nothing having
      named every spelling of ``set_config``, so it is what still stands now that the catalogue has
      replaced author-written SQL: a rewrite reached through a view's function or a custom operator
      is caught here or nowhere.

    ``scope`` is keyword-only and has NO DEFAULT on purpose. It used to default to ``global``, so a
    caller that simply forgot it got minted claims carrying ``contacts:read_all`` and the firm-wide
    path — an escalation by omission.

    Everything runs inside a SAVEPOINT that is rolled back afterwards, restoring the ambient role,
    claims and settings (a SELECT changed nothing else).
    """
    # The binds the SQL actually names, against the ones we are about to pass. See
    # :class:`DatasetBindMismatch` — a mismatch here is a syntax error there.
    named = set(text(stmt)._bindparams)
    if named != set(params):
        raise DatasetBindMismatch(f"query binds {sorted(named)} but was given {sorted(params)}")

    role, ambient = await _session_principal(session)
    borrowed = False
    if claims is not None:
        effective: str | None = json.dumps(claims)
    elif ambient:
        # A session that ALREADY has an identity keeps it, whatever its DB role. Minting for a
        # service_role session that carries claims (``firm_session``, a client-portal path) would
        # WIDEN a real principal to firm-wide on the strength of a dataset's own label — the
        # escalation this module spent a wave closing, re-entered through the back door.
        effective = None
        # …but only the request hot path (`authenticated` + claims: a person on their own screen) is
        # a context where "your scope, not the firm's" is the right answer. Anywhere else, falling
        # short of a `global` dataset's declared scope is a fault, not a caveat.
        borrowed = role == "authenticated"
    else:
        # Read before the savepoint and before the downgrade: this establishes the claims, so it
        # cannot run under them.
        accounts = (
            await _subject_account_ids(session, tenant_id, subject_id) if scope == "subject" else []
        )
        effective = json.dumps(_minted_claims(tenant_id, subject_id, scope, accounts))

    narrowed = False
    savepoint = await session.begin_nested()
    try:
        # Claims FIRST, then the role: publishing them as the ambient role keeps the door open to
        # the structural fix for this whole class of bug — REVOKE EXECUTE ON set_config FROM
        # authenticated, after which no SELECT reached from here can rewrite the GUC at all.
        if effective is not None:
            await session.execute(
                text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": effective}
            )
        await session.execute(text("SET LOCAL ROLE authenticated"))
        # Constants, not binds: SET takes no parameters. Both are transaction-local and unwound by
        # the savepoint rollback below.
        await session.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
        await session.execute(text("SET LOCAL transaction_read_only = on"))
        if scope != "subject":
            # Can the identity this query is about to run as actually see the firm the dataset
            # claims to describe? Asked of the database (``auth.can_read_all()`` is what every RLS
            # policy consults) rather than by re-reading the claims dict here, so it cannot drift
            # from the predicate that will do the filtering.
            narrowed = not bool(
                (await session.execute(text("SELECT auth.can_read_all()"))).scalar()
            )
        result = await session.execute(text(_wrap_with_cap(stmt, row_limit)), params)
        rows = [dict(r) for r in result.mappings().all()]
        # The rows are in memory but have NOT been handed to the caller yet: if the statement moved
        # the role or the claims every RLS predicate reads, they were scanned under an identity the
        # query chose for itself, so they are not this caller's rows. Refuse instead of returning
        # them (the savepoint below puts the session back either way).
        role_after, claims_after = await _session_principal(session)
        expected = effective if effective is not None else ambient
        if role_after != "authenticated" or claims_after != expected:
            raise DatasetSqlError(
                "The query changed the session's identity (role or request.jwt.claims) while it "
                "ran, so its rows can't be trusted and were discarded. Remove any set_config() or "
                "SET from the query."
            )
    finally:
        # ROLLBACK TO SAVEPOINT reverts the role, the claims and both settings.
        await savepoint.rollback()
    if len(rows) > row_limit:
        return _SqlRun(rows[:row_limit], True, narrowed=narrowed, borrowed=borrowed)
    return _SqlRun(rows, False, narrowed=narrowed, borrowed=borrowed)


async def _resolve_catalogue(
    session: AsyncSession,
    entry: catalogue.Entry,
    params: dict[str, Any] | None,
    subject_id: Any,
    tenant_id: int,
    *,
    claims: dict[str, Any] | None = None,
    today: date | None = None,
) -> _SqlRun:
    """Run a catalogue entry with the firm's chosen parameters.

    The entry's scope is authoritative — it is declared in code beside the SQL that binds (or does
    not bind) the client, so the two cannot disagree. A stored row's own ``scope`` column is not
    consulted: it is author-supplied, and letting it say ``global`` for a ``subject`` entry is
    exactly the mint-``contacts:read_all`` escalation ``_execute_scoped`` refuses to default into.
    """
    stmt, binds = catalogue.compile_entry(entry, params, today=today or date.today())
    # Only what the SQL actually names: `_execute_scoped` asserts the two match exactly, so
    # supplying a bind the entry does not use is as much an error as omitting one it does.
    if f":{catalogue.SUBJECT_BIND}" in stmt:
        binds[catalogue.SUBJECT_BIND] = subject_id
    if f":{catalogue.TENANT_BIND}" in stmt:
        binds[catalogue.TENANT_BIND] = tenant_id
    return await _execute_scoped(
        session,
        stmt,
        binds,
        tenant_id=tenant_id,
        subject_id=subject_id,
        scope=entry.scope,
        claims=claims,
    )


async def resolve_datasets_reporting(
    session: AsyncSession,
    tenant_id: int,
    codes: list[str],
    subject_id: Any = None,
    *,
    claims: dict[str, Any] | None = None,
    speculative: Collection[str] | None = None,
    today: date | None = None,
) -> DatasetResolution:
    """Resolve each dataset code to its rows, reporting every code that could not be resolved.

    A code always gets an entry in ``rows`` (``[]`` when it failed) so a component binding it still
    renders its empty state — but the failure is no longer invisible: it comes back in ``problems``
    for the caller to fail the render on, or record against the document.

    ``speculative`` names codes that were DISCOVERED rather than declared — a bare
    ``{% for x in y %}`` source, where the identifier is just as likely a form answer or a context
    variable as a dataset code (see ``components.loop_sources``). For those, "no such dataset" is
    reported as advisory, never blocking: a declared link or a component binding is a contract the
    author made, but a loop identifier is a guess, and failing a render over a guess breaks every
    template that loops over its own inputs.
    """
    rows: dict[str, list[dict[str, Any]]] = {}
    problems: list[DatasetProblem] = []

    def fail(code: str, reason: str, detail: str) -> None:
        rows[code] = []
        problems.append(DatasetProblem(code=code, reason=reason, detail=detail))
        logger.warning("dataset %r unresolved (%s): %s", code, reason, detail)

    def advise(code: str, reason: str, detail: str) -> None:
        """An advisory problem: the rows ARE genuine, there are just none of them (``empty``) or not
        all of them (``truncated``). Logged as well as reported, so a caller that only takes the
        rows still leaves a trace of why a document's tables came out blank or short."""
        problems.append(DatasetProblem(code=code, reason=reason, detail=detail))
        logger.info("dataset %r %s: %s", code, reason, detail)

    guessed = set(speculative or ())

    for code in dict.fromkeys(c for c in codes if c):  # de-dupe, preserve order
        try:
            source = await _dataset_source(session, tenant_id, code)
            if source is None:
                if code in guessed:
                    # A loop source that is not a dataset. Almost always intentional (the loop
                    # iterates an input or an answer); occasionally the typo the audit called out.
                    # Advisory either way -- traceable, but it cannot fail the render.
                    advise(code, "not_a_dataset", f"Loop source {code!r} is not a firm dataset.")
                    rows.setdefault(code, [])
                    continue
                fail(code, "missing", f"No dataset with code {code!r} in this firm.")
                continue
            if not source["enabled"]:
                fail(code, "disabled", f"Dataset {code!r} is disabled.")
                continue
            if not source["published"]:
                fail(
                    code,
                    "unpublished",
                    f"Dataset {code!r} has no published version."
                    if source["has_versions"]
                    else f"Dataset {code!r} has no data yet.",
                )
                continue
            fmt = source["format"]
            if fmt == "inline" and source["scope"] == "subject":
                # A STATIC source cannot be per-client: a stored table is the same rows for
                # everybody, so resolving one into a single client's document is exactly the
                # firm-wide-rows leak `scope='subject'` exists to prevent. Named by format rather
                # than as "not catalogue", so a catalogue entry — which is per-client by binding
                # its own `:subject_id` — is not swept up by it.
                fail(
                    code,
                    "scope_mismatch",
                    f"Dataset {code!r} is per-client, but a stored table is the same rows for "
                    "every client — so it would put the whole firm's rows in one client's "
                    "document. Make it firm-wide, or use one of the provided per-client datasets.",
                )
                continue
            if fmt == "catalogue":
                # A catalogue entry carries its own scope, its own binds and its own WHERE, so none
                # of the scope reconciliation below applies: there is no author SQL to disagree with
                # a stored `scope` column. Handled before those branches rather than after, because
                # `_dataset_source`'s `scope` for an unconfigured entry IS the entry's.
                entry = catalogue.by_key(source.get("catalogue_key") or code)
                if entry is None:
                    # A stored row naming an entry that no longer ships. Blocking and named: the
                    # alternative is an empty table where a client expects their valuations.
                    fail(
                        code,
                        "missing",
                        f"Dataset {code!r} uses {source.get('catalogue_key')!r}, which this "
                        "version of the platform no longer provides.",
                    )
                    continue
                if entry.scope == "subject" and subject_id is None:
                    fail(
                        code,
                        "missing_subject",
                        f"Dataset {code!r} is per-client, but this render has no client to resolve "
                        "it for.",
                    )
                    continue
                run = await _resolve_catalogue(
                    session,
                    entry,
                    source.get("catalogue_params"),
                    subject_id,
                    tenant_id,
                    claims=claims,
                    today=today,
                )
                rows[code] = run.rows
                if run.truncated:
                    advise(
                        code,
                        "truncated",
                        f"Dataset {code!r} returned more than {DATASET_ROW_LIMIT} rows; only the "
                        "first were used. Narrow the date range.",
                    )
                if run.narrowed and run.borrowed:
                    # A preview running as the caller. Their own scope IS the right answer for
                    # their screen, so this is advisory — but it must be SAID, because the same
                    # dataset in a generated document resolves across the whole firm and will show
                    # more. Without it an adviser sees their own book, is told nothing, and has no
                    # way to know the document will not match what they just looked at.
                    advise(
                        code,
                        "preview_scope",
                        f"Dataset {code!r} is firm-wide, but you can only see your own clients — "
                        "so this may show fewer rows here than in a generated document, which "
                        "resolves it across the whole firm.",
                    )
                elif run.narrowed:
                    # A background resolve under claims that cannot see the firm. Nobody is looking
                    # at a screen: these rows would go into a document as if firm-wide.
                    fail(
                        code,
                        "scope_narrowed",
                        f"Dataset {code!r} is firm-wide but the identity it ran under can only see "
                        "part of the firm, so its rows would be a subset presented as the whole.",
                    )
                    continue
            elif fmt == "inline":
                rows[code] = rows_from_static(source["inline_data"])
            else:  # pragma: no cover — the enum has only the two members handled above
                fail(code, "unsupported_format", f"Dataset {code!r} has an unknown format {fmt!r}.")
                continue
        except DatasetSqlError as exc:
            fail(code, "rejected", str(exc))
        except Exception as exc:  # noqa: BLE001 — one bad dataset must not kill the whole render
            # The driver message can carry the firm's data, so it is logged, not reported. And the
            # advice is no longer "check the query": the query is OURS now, so telling a firm to
            # check it sends them to look at something they cannot see or change.
            logger.warning("dataset %r failed to resolve: %s", code, exc)
            fail(code, "error", f"Dataset {code!r} could not be read.")
        else:
            if not rows.get(code):
                # Not fatal on its own, but the reason a document ships with blank tables — so the
                # caller gets told rather than discovering it in a client's PDF.
                advise(code, "empty", f"Dataset {code!r} returned no rows.")
    return DatasetResolution(rows=rows, problems=problems)


async def resolve_datasets(
    session: AsyncSession,
    tenant_id: int,
    codes: list[str],
    subject_id: Any = None,
    *,
    claims: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Rows-only wrapper around ``resolve_datasets_reporting`` for callers that don't consume
    problems yet. Every problem — including the advisory ``empty``/``truncated`` ones — is
    summarised in one WARNING here, so a blank table in a shipped document is at least traceable in
    the worker log. Prefer ``resolve_datasets_reporting``: a log line is not a report to the person
    the document is for."""
    resolution = await resolve_datasets_reporting(
        session, tenant_id, codes, subject_id, claims=claims
    )
    if resolution.problems:
        logger.warning(
            "dataset resolution finished with %d problem(s): %s",
            len(resolution.problems),
            "; ".join(f"{p.code}={p.reason}" for p in resolution.problems),
        )
    return resolution.rows
