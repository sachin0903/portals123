"""Datasets — the data sources a document template draws on at render time.

A dataset's FORMAT says where its rows come from, and there are two:

* ``catalogue`` — a query GRAPHENE wrote (``app/render/catalogue.py``), which a firm picks and
  configures (a date range). Entries live in code, so every firm has them without doing anything
  and none of them can drift per tenant; ``GET /datasets`` lists them alongside a firm's own rows
  and ``GET /datasets/catalogue`` describes what each one lets a firm set.
* ``inline`` — a table of rows the firm typed in (or imported from a CSV client-side), stored as a
  snapshot.

FREE-TEXT SQL AUTHORING IS GONE (0116). A firm could type any SELECT, and the only thing between
that query and the database was a keyword denylist, a function denylist, a hand-written lexer and a
bind allowlist — guarding a query no reviewer had seen. The replacement is a smaller question
rather than a better validator: a firm chooses an entry and its settings, and a setting is a VALUE
bound by the driver, never text spliced into SQL. No setting names a table, a column, an ordering
or a predicate. The unreachable server-side ``csv`` format went with it (no upload endpoint ever
existed, and the working CSV import is client-side and lands as ``inline``).

``scope`` is ``global`` (the same rows for every client) or ``subject`` (about one client). It is
not a label: a ``global`` dataset is resolved FIRM-WIDE at render
(``render/resolve.py::_minted_claims``), which is why configuring one takes ``contacts:read_all`` as
well as ``datasets:write`` (``_require_firm_wide_author``) — otherwise a book-scoped author could
read the firm's whole client book out of any document generated from their own dataset. Only a
catalogue entry can be per-client, because only its SQL binds the render's client; and the ENTRY's
declared scope wins over anything a request says, since a stored ``global`` on a per-client entry
would hand the query firm-wide claims.

Tenant-scoped under ``tenant_session`` (RLS ``authenticated``); reads gated by ``datasets:read``,
authoring by ``datasets:write``. A firm's chosen settings are validated on save against the entry's
own declaration — an unknown setting name is refused rather than dropped, because a setting that
silently does nothing is the failure mode that reads as success. Every write here is captured by the
DML audit trigger on ``documents.datasets`` / ``documents.dataset_versions`` (``audit.attach``,
0012), so who configured what is on record.

Endpoints: list (format filter) · list the provided catalogue · get · get resolved data (snapshot) ·
list subjects · create · update · delete (refused while templates use it) · add a version · list
versions. Dev data comes from ``scripts/seed_datasets.py``.
"""

import json
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, get_current_principal, is_system, require_permission
from app.db import target_tenant_session as tenant_session
from app.ids import new_id
from app.permissions import CONTACTS_READ_ALL, DATASETS_READ, DATASETS_WRITE, satisfies
from app.render import catalogue
from app.render.resolve import (
    DATASET_ROW_LIMIT,
    resolve_datasets_reporting,
)

router = APIRouter(prefix="/datasets", tags=["datasets"])

# A dataset + creator name + version count + latest published version number.
_SELECT = (
    "SELECT d.uid, d.code, d.name, d.description, d.format::text AS format, "
    "       d.scope::text AS scope, d.enabled, d.created_at, "
    "       u.name AS created_by, "
    "       (SELECT v.catalogue_ref ->> 'key' FROM documents.dataset_versions v "
    "         WHERE v.dataset_id = d.id "
    "         ORDER BY (v.status = 'published') DESC, v.version_number DESC LIMIT 1) "
    "         AS catalogue_key, "
    "       (SELECT count(*) FROM documents.dataset_versions v WHERE v.dataset_id = d.id) "
    "         AS version_count, "
    "       (SELECT max(version_number) FROM documents.dataset_versions v "
    "         WHERE v.dataset_id = d.id AND v.status = 'published') AS published_version_number "
    "FROM documents.datasets d "
    "LEFT JOIN auth.users u ON u.id = d.created_by "
)

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
_FORMATS = {"catalogue", "inline"}
# 'collection' exists in the DB enum but has no defined semantics anywhere (nothing resolves a
# collection), so it is refused rather than offered as a knob that does nothing.
_SCOPES = {"subject", "global"}


class DatasetOut(BaseModel):
    uid: str
    code: str
    name: str
    description: str | None
    format: str
    scope: str
    enabled: bool
    version_count: int
    published_version_number: int | None
    created_at: datetime
    created_by: str | None
    #: Shipped by Graphene rather than authored by the firm. Such an item has no row in
    #: ``documents.datasets`` at all — its ``uid`` is the catalogue key — so it cannot be edited or
    #: deleted, and it is usable by every firm the moment it ships.
    provided: bool = False
    #: The catalogue entry this draws on: its own key when ``provided``, or the key a firm's
    #: configured row was built from.
    catalogue_key: str | None = None


class DatasetParamOut(BaseModel):
    """One setting a firm may choose on a provided dataset."""

    name: str
    kind: str
    label: str
    help: str = ""
    #: The shipped default, VERBATIM — a date may be a relative token (``-12m``) rather than a date,
    #: because it is resolved on every render so a configured range stays current.
    default: Any = None
    options: list[dict[str, str]] = []
    minimum: int | None = None
    maximum: int | None = None


class DatasetColumnOut(BaseModel):
    name: str
    label: str
    kind: str


class CatalogueEntryOut(BaseModel):
    """A dataset Graphene provides, with what a firm may set on it."""

    key: str
    name: str
    description: str
    note: str = ""
    scope: str
    columns: list[DatasetColumnOut]
    params: list[DatasetParamOut]
    #: A permission the author needs to use this entry. ``None`` means any dataset author may.
    entitlement: str | None = None


class DatasetSample(BaseModel):
    """Tabular data — columns + row arrays. The stored shape for ``inline`` datasets, and the
    response shape of the resolved-data (snapshot) endpoint."""

    columns: list[str]
    rows: list[list[Any]]


class DatasetDataOut(DatasetSample):
    """A resolved snapshot plus the honest caveats: whether the row cap clipped the result, what
    that cap is, and a note when there is nothing to show (so an empty grid is never mistaken for
    "no data exists")."""

    truncated: bool = False
    row_limit: int = DATASET_ROW_LIMIT
    notice: str | None = None


class DatasetDetailOut(DatasetOut):
    sample: DatasetSample | None = None
    # The column schema the chart/table field pickers read: inline → the sample's columns;
    # catalogue → the entry's own declaration (there is nothing to parse, and nothing to guess).
    columns: list[str] = []
    # Live templates that declare this dataset — deletion is refused while any exist, and the
    # confirm dialog tells the author the number before they try.
    used_by_templates: int = 0


class DatasetSubjectOut(BaseModel):
    """A client the author can resolve a subject-scoped dataset against ("Run query")."""

    contact_id: int
    name: str


class CatalogueRef(BaseModel):
    """Which provided dataset a firm has configured, and how.

    ``params`` is stored VERBATIM, so a relative range (``-12m``) keeps meaning "the last twelve
    months" on every render rather than freezing on the day it was set. What names and values are
    accepted comes from the entry's own declaration in `app/render/catalogue.py`, validated on save
    and again on compile.
    """

    key: str
    params: dict[str, Any] | None = None


class DatasetCreate(BaseModel):
    code: str
    name: str
    description: str | None = None
    format: str
    scope: str = "global"
    # Exactly one of these per format:
    inline: DatasetSample | None = None
    catalogue: CatalogueRef | None = None
    # Optional preview-only sample rows (any format) — shown in the editor preview, never resolved
    # at generate. For `inline` datasets the inline data already serves as the sample.
    sample: DatasetSample | None = None


class DatasetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    scope: str | None = None


class DatasetVersionCreate(BaseModel):
    inline: DatasetSample | None = None
    catalogue: CatalogueRef | None = None
    sample: DatasetSample | None = None  # preview-only, as in DatasetCreate


class DatasetListItem(DatasetOut):
    """A list row plus the preview the template editor's pickers need, so opening the editor
    is one round-trip rather than one GET per dataset."""

    columns: list[str] = []
    sample: DatasetSample | None = None


class DatasetVersionOut(BaseModel):
    """A row in a dataset's authoring history (the current version is the highest number). Carries
    the source (the table's rows, or the catalogue reference and its settings) so the edit form can
    seed from the current version."""

    uid: str
    version_number: int
    status: str
    created_at: datetime
    published_at: datetime | None
    created_by: str | None
    inline: DatasetSample | None = None
    catalogue: CatalogueRef | None = None


def _sample_from_inline(inline_data: Any) -> DatasetSample | None:
    """Shape a version's ``inline_data`` jsonb (``{columns, rows}`` or a list of row-objects) into a
    columns/rows preview."""
    if isinstance(inline_data, dict):
        cols = inline_data.get("columns")
        rows = inline_data.get("rows")
        if isinstance(cols, list) and isinstance(rows, list):
            return DatasetSample(columns=[str(c) for c in cols], rows=[list(r) for r in rows])
    if isinstance(inline_data, list) and inline_data and isinstance(inline_data[0], dict):
        columns = list(inline_data[0].keys())
        return DatasetSample(
            columns=columns, rows=[[row.get(c) for c in columns] for row in inline_data]
        )
    return None


async def _tenant_id(session: AsyncSession) -> int:
    tid = (await session.execute(text("SELECT auth.tenant_id()"))).scalar_one_or_none()
    if tid is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tenant in context")
    return int(tid)


async def _assert_visible_contact(session: AsyncSession, contact_id: int) -> None:
    """422 unless contact_id is a crm.contacts row the caller may see (RLS-scoped) — mirrors
    files.py::_assert_visible_contact, so a subject-scoped dataset can only be resolved for a client
    the caller actually has access to (a caller-supplied :subject_id is otherwise an IDOR)."""
    ok = (
        await session.execute(
            text("SELECT 1 FROM crm.contacts WHERE id = :cid"), {"cid": contact_id}
        )
    ).scalar_one_or_none()
    if ok is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Unknown client, or you don't have access to them.",
        )


async def _template_uses(session: AsyncSession, code: str) -> int:
    """How many live (un-archived) templates declare this dataset code. RLS-scoped, so it counts
    only this firm's templates."""
    return int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM documents.templates t "
                    "WHERE :code = ANY (t.linked_datasets) AND t.archived_at IS NULL"
                ),
                {"code": code},
            )
        ).scalar_one()
    )


def _assert_scope_supported(fmt: str, scope: str) -> None:
    """Refuse ``scope='subject'`` on a format that cannot be scoped to a client.

    Only a CATALOGUE entry takes the render's client: its SQL binds ``:subject_id`` itself, and the
    entry declares whether it does. An inline table is the same rows for everybody, so a
    "client-scoped" one resolves the whole thing into one client's document — the very leak the
    scope axis exists to prevent (the resolver refuses it too, for rows created before this guard).
    """
    if scope == "subject" and fmt != "catalogue":
        raise HTTPException(
            422,
            "Only a provided dataset can be per-client — a table you type in is the same rows for "
            "every client, so 'subject' would put the whole firm's rows in one client's document. "
            "Use scope 'global', or pick one of the provided per-client datasets.",
        )


def _require_firm_wide_author(principal: Principal, fmt: str, scope: str) -> None:
    """Configuring a ``global`` CATALOGUE dataset needs ``contacts:read_all`` as well as
    ``datasets:write``.

    Why: ``scope`` decides who the query runs as at render. A ``global`` dataset is resolved
    firm-wide (``render/resolve.py::_minted_claims`` mints ``contacts:read_all`` for the dataset's
    own tenant), which is the only way "firm-wide" can be honest — before that it was silently
    narrowed to the document's subject client. So the entitlement to declare firm-wide data is the
    entitlement to see firm-wide data: a book-scoped author must not be able to configure their way
    to the firm's whole book and read it out of a generated document, where the escalation would be
    invisible because the authoring preview runs under their OWN claims.

    RE-AIMED FROM ``sql`` TO ``catalogue``, deliberately. The gate used to open with
    ``if fmt != "sql"``, and free-text SQL is gone — so left as it was it would have stopped firing
    at all while the escalation it exists to stop was unchanged: a ``global`` catalogue entry is
    minted exactly the same ``contacts:read_all`` claims. An inline table stays ungated: it carries
    no query, so nothing is executed on the firm's behalf and a global inline table is just rows
    its author typed in.

    403 (not 422): the request is well-formed, the caller is not allowed to make it. The message
    says what to do instead — the per-client entries stay open to every author.
    """
    if fmt != "catalogue" or scope == "subject":
        return
    if is_system(principal) or CONTACTS_READ_ALL.slug in principal.permissions:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "A firm-wide dataset is run across the whole firm's data when a document is generated, "
            f"so setting one up needs the 'Read all clients' permission ({CONTACTS_READ_ALL.slug}) "
            "as well as dataset authoring. Use a per-client dataset instead, or ask an "
            "administrator for full-book access."
        ),
    )


def _resolve_entry(ref: CatalogueRef | None) -> catalogue.Entry:
    """The catalogue entry a configured dataset names, or a 422 that lists what is available."""
    if ref is None or not (ref.key or "").strip():
        raise HTTPException(422, "A provided dataset needs a catalogue key.")
    entry = catalogue.by_key(ref.key)
    if entry is None:
        raise HTTPException(
            422,
            f"There is no provided dataset called {ref.key!r}. "
            f"Available: {', '.join(sorted(catalogue.keys()))}.",
        )
    return entry


def _validate_source(
    fmt: str,
    inline: Any,
    ref: CatalogueRef | None,
    scope: str,
    principal: Principal,
) -> None:
    """Exactly one source must match the format (mirrors the DB CHECK, with clean errors).

    For a CATALOGUE dataset that means the entry exists and the firm's chosen parameters are ones
    it accepts — checked here so the refusal lands on the person who typed them rather than on a
    document a year later. Parameters are validated, never coerced: an unknown setting name is
    refused rather than dropped, because a setting that silently does nothing is the failure mode
    that reads as success.

    The ENTRY's declared scope wins over anything the request says. A stored ``scope`` of
    ``global`` on a per-client entry would hand the query ``contacts:read_all`` at render, which is
    exactly the escalation ``_require_firm_wide_author`` exists to stop.
    """
    if fmt == "catalogue":
        entry = _resolve_entry(ref)
        if entry.entitlement and not (
            is_system(principal) or satisfies(set(principal.permissions), entry.entitlement)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires the '{entry.entitlement}' permission",
            )
        if scope != entry.scope:
            raise HTTPException(
                422,
                f"{entry.name} is {'per-client' if entry.scope == 'subject' else 'firm-wide'}, so "
                f"its scope must be {entry.scope!r}.",
            )
        try:
            catalogue.validate_params(entry, ref.params if ref else None)
        except catalogue.ParamError as exc:
            raise HTTPException(422, str(exc)) from exc
        return
    _assert_scope_supported(fmt, scope)
    if fmt == "inline":
        if inline is None:
            raise HTTPException(422, "A table dataset needs some rows")
        return
    raise HTTPException(422, f"Unknown format {fmt!r}")


async def _insert_version(
    session: AsyncSession,
    dataset_id: int,
    version_number: int,
    fmt: str,
    inline: DatasetSample | None,
    ref: CatalogueRef | None,
    sample: DatasetSample | None = None,
) -> None:
    """Insert a published dataset version carrying the one source its format requires, plus an
    optional preview-only ``sample_data`` blob (never resolved at generate).

    A catalogue reference stores the firm's parameters VERBATIM — ``-12m`` stays ``-12m``, because
    a relative range is resolved on every render and storing the date it happened to mean today
    would freeze the document on one fixed period.
    """
    inline_json = json.dumps(inline.model_dump()) if (fmt == "inline" and inline) else None
    ref_json = (
        json.dumps({"key": ref.key, "params": ref.params or {}})
        if (fmt == "catalogue" and ref)
        else None
    )
    sample_json = json.dumps(sample.model_dump()) if sample else None
    await session.execute(
        text(
            "INSERT INTO documents.dataset_versions "
            "  (uid, tenant_id, dataset_id, version_number, status, format, "
            "   inline_data, catalogue_ref, sample_data, created_by, published_at) "
            "VALUES (:uid, auth.tenant_id(), :did, :vn, 'published', "
            "  CAST(:fmt AS documents.dataset_format), CAST(:inline AS jsonb), "
            "  CAST(:ref AS jsonb), CAST(:sample AS jsonb), auth.uid(), now())"
        ),
        {
            "uid": new_id(),
            "did": dataset_id,
            "vn": version_number,
            "fmt": fmt,
            "inline": inline_json,
            "ref": ref_json,
            "sample": sample_json,
        },
    )


# ── reads ─────────────────────────────────────────────────────────────────────
# An unrecognised filter value is REFUSED below rather than ignored: silently dropping
# `?format=inlien` returns the whole catalogue, which reads as "this firm has no table datasets
# either way" — the same class of quiet wrongness as an unresolved dataset rendering blank.
#
# There is no `liveness` filter any more. It was derived from the format rather than chosen, so it
# was always 1:1 with this one, and 0116 dropped the column: two controls that mean the same thing
# invite the reader to think they don't.
#
# (Both notes live here rather than in the docstring: FastAPI publishes an endpoint's docstring as
# its OpenAPI description and the generated TS client carries it as a JSDoc comment — so editing one
# breaks the api-client drift check until the client is regenerated.)
@router.get("", name="list-datasets")
async def list_datasets(
    # Searched in SQL rather than in the browser: the page filters `name` and `code`, and
    # doing that here is what makes its question reach past the rows already sent.
    q: str | None = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
    format: str | None = Query(default=None, description="Filter by catalogue|inline"),
    include: str | None = Query(
        default=None, description="Pass 'sample' to include preview columns/rows for pickers."
    ),
) -> list[DatasetOut] | list[DatasetListItem]:
    """The firm's datasets (RLS-scoped) plus the ones Graphene provides, newest first."""
    require_permission(principal, DATASETS_READ)
    where: list[str] = []
    params: dict[str, Any] = {}
    # `isinstance(..., str)`, not `is not None`: these coroutines are also called directly (tests,
    # and any internal reuse), where an omitted argument is FastAPI's `Query(...)` sentinel object
    # rather than None. Treating that as "a filter was supplied" would 422 every unfiltered list.
    if isinstance(format, str):
        if format not in _FORMATS:
            raise HTTPException(422, f"format must be one of: {', '.join(sorted(_FORMATS))}.")
        where.append("d.format = CAST(:format AS documents.dataset_format)")
        params["format"] = format
    if q and q.strip():
        where.append("(d.name ILIKE :q OR d.code ILIKE :q)")
        params["q"] = f"%{q.strip()}%"
    sql = _SELECT + (("WHERE " + " AND ".join(where) + " ") if where else "")
    sql += "ORDER BY d.created_at DESC, d.id DESC"
    rows = (await session.execute(text(sql), params)).mappings().all()
    listed: list[DatasetOut] = [DatasetOut(**r) for r in rows]
    # …then the entries Graphene PROVIDES. They have no row of their own, so they are appended
    # rather than queried: a firm can use one the moment it ships, and the merge picker (which
    # reads this endpoint) offers it without the firm having to configure anything first. A firm
    # that DOES configure one gets a separate row of its own above — `_CODE_RE` forbids the dot, so
    # a firm's code can never collide with a `book.*` key and neither hides the other.
    # EVERY filter applies to them as it does to a firm's own rows — filtering the DB and then
    # appending unconditionally is how a filter comes to mean nothing. That included the SEARCH: it
    # is applied in SQL above, so a provided entry was appended whatever had been typed, and a
    # search for one dataset came back with the whole catalogue underneath it. Matched on the same
    # two fields the SQL matches, so the two halves of one list answer the same question.
    if not isinstance(format, str) or format == "catalogue":
        needle = q.strip().lower() if q and q.strip() else None
        provided = [
            _provided_out(e)
            for e in catalogue.all_entries()
            if e.entitlement is None
            or is_system(principal)
            or satisfies(set(principal.permissions), e.entitlement)
        ]
        listed += [
            d
            for d in provided
            if needle is None
            or needle in (d.name or "").lower()
            or needle in (d.code or "").lower()
        ]
    if include != "sample" or not listed:
        return listed
    previews = await _list_previews(session, [d.uid for d in listed if not d.provided])
    out: list[DatasetListItem] = []
    for d in listed:
        if d.provided:
            entry = catalogue.by_key(d.catalogue_key or d.uid)
            # DECLARED columns, not sniffed ones. This is the whole reason the picker can offer a
            # provided dataset's fields at all: there is no SQL to parse a SELECT list out of, and
            # `_columns_from_sql` returned `[]` for a `SELECT *` anyway — which rendered a field
            # picker with nothing in it and no error to say why.
            cols = [c.name for c in entry.columns] if entry else []
            out.append(DatasetListItem(**d.model_dump(), columns=cols, sample=None))
            continue
        cols, sample = previews.get(d.uid, ([], None))
        out.append(DatasetListItem(**d.model_dump(), columns=cols, sample=sample))
    return out


def _entry_out(entry: catalogue.Entry) -> CatalogueEntryOut:
    return CatalogueEntryOut(
        key=entry.key,
        name=entry.name,
        description=entry.description,
        note=entry.note,
        scope=entry.scope,
        columns=[DatasetColumnOut(name=c.name, label=c.label, kind=c.kind) for c in entry.columns],
        params=[
            DatasetParamOut(
                name=p.name,
                kind=p.kind,
                label=p.label,
                help=p.help,
                default=p.default,
                options=[{"value": v, "label": lbl} for v, lbl in p.options],
                minimum=p.minimum,
                maximum=p.maximum,
            )
            for p in entry.params
        ],
        entitlement=entry.entitlement,
    )


def _provided_out(entry: catalogue.Entry) -> DatasetOut:
    """A shipped entry in the shape the datasets listing and the merge picker already read.

    ``uid`` IS the catalogue key. There is no row to take a uid from, and the key is a safe
    identifier to put in that position because ``_CODE_RE`` forbids the dot — so a firm's own
    dataset can never carry a code that collides with one.

    ``created_at`` is epoch so these sort to the bottom of a newest-first listing: they are always
    there, and a firm's own work is what changed.
    """
    return DatasetOut(
        uid=entry.key,
        code=entry.key,
        name=entry.name,
        description=entry.description,
        format="catalogue",
        scope=entry.scope,
        enabled=True,
        version_count=0,
        published_version_number=None,
        created_at=datetime(1970, 1, 1, tzinfo=UTC),
        created_by="Graphene",
        provided=True,
        catalogue_key=entry.key,
    )


# Declared before "/{uid}" so the literal path isn't swallowed by the uid route.
@router.get("/catalogue", name="list-dataset-catalogue")
async def list_dataset_catalogue(
    principal: Principal = Depends(get_current_principal),
) -> list[CatalogueEntryOut]:
    """The datasets Graphene provides, and what a firm may set on each.

    No session and no tenant scoping: entries are code-side, identical for every firm, and contain
    no firm data — only the shape of a query. What the query then RETURNS is scoped at resolve
    time by the entry's own WHERE plus RLS.
    """
    require_permission(principal, DATASETS_READ)
    return [
        _entry_out(e)
        for e in catalogue.all_entries()
        if e.entitlement is None
        or is_system(principal)
        or satisfies(set(principal.permissions), e.entitlement)
    ]


# Declared before "/{uid}" so the literal path isn't swallowed by the uid route.
@router.get("/subjects", name="list-dataset-subjects")
async def list_dataset_subjects(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[DatasetSubjectOut]:
    """Clients a subject-scoped dataset can be resolved against ("Run query"). RLS-scoped, so an
    author sees their own book (or the whole firm with ``contacts:read_all``) — the same scope the
    query itself will run under."""
    require_permission(principal, DATASETS_READ)
    rows = (
        (
            await session.execute(
                text(
                    "SELECT c.id AS contact_id, c.name FROM crm.contacts c "
                    "WHERE c.enabled AND c.deleted_at IS NULL "
                    "ORDER BY c.surname NULLS LAST, c.name NULLS LAST, c.id LIMIT 500"
                )
            )
        )
        .mappings()
        .all()
    )
    return [DatasetSubjectOut(**r) for r in rows]


async def _list_previews(
    session: AsyncSession, uids: list[str]
) -> dict[str, tuple[list[str], DatasetSample | None]]:
    """Latest-version preview columns/sample for many datasets in one query (no CSV-from-S3)."""
    if not uids:
        return {}
    placeholders = ", ".join(f":u{i}" for i in range(len(uids)))
    params = {f"u{i}": uid for i, uid in enumerate(uids)}
    rows = (
        (
            await session.execute(
                text(
                    # `placeholders` is `:u0, :u1, …` built from range(len(uids)); the uids
                    # themselves are bound parameters, never interpolated.
                    "SELECT d.uid, d.format::text AS format, "  # noqa: S608
                    "       v.inline_data, v.catalogue_ref ->> 'key' AS catalogue_key, "
                    "       v.sample_data "
                    "FROM documents.datasets d "
                    "JOIN LATERAL ("
                    "  SELECT inline_data, catalogue_ref, sample_data "
                    "  FROM documents.dataset_versions v "
                    "  WHERE v.dataset_id = d.id "
                    "  ORDER BY (v.status='published') DESC, v.version_number DESC "
                    "  LIMIT 1"
                    ") v ON true "
                    f"WHERE d.uid IN ({placeholders})"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    out: dict[str, tuple[list[str], DatasetSample | None]] = {}
    for row in rows:
        sample: DatasetSample | None = None
        columns: list[str] = []
        if row["format"] == "inline":
            sample = _sample_from_inline(row["inline_data"])
            columns = sample.columns if sample else []
        else:
            sample = _sample_from_inline(row["sample_data"]) if row["sample_data"] else None
            # DECLARED columns, from the entry. There is nothing to parse and nothing to sniff:
            # the old path read them out of a SELECT list and returned [] for a `SELECT *`, which
            # handed the merge picker an empty field list with no error to explain it.
            entry = catalogue.by_key(row["catalogue_key"] or "")
            if entry is not None:
                columns = [c.name for c in entry.columns]
            if not columns and sample:
                columns = sample.columns
        out[row["uid"]] = (columns, sample)
    return out


async def _load_detail(session: AsyncSession, uid: str) -> DatasetDetailOut:
    row = (
        (await session.execute(text(_SELECT + "WHERE d.uid = :uid"), {"uid": uid}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    sample: DatasetSample | None = None
    columns: list[str] = []
    ver = (
        (
            await session.execute(
                text(
                    "SELECT inline_data, catalogue_ref, sample_data "
                    "FROM documents.dataset_versions v "
                    "JOIN documents.datasets d ON d.id = v.dataset_id "
                    "WHERE d.uid = :uid "
                    "ORDER BY (v.status='published') DESC, v.version_number DESC LIMIT 1"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if ver:
        if row["format"] == "inline":
            # Inline data IS the dataset — it doubles as the preview sample.
            sample = _sample_from_inline(ver["inline_data"])
            columns = sample.columns if sample else []
        else:
            # catalogue: the author's preview sample (if any); columns from the entry's declaration.
            sample = _sample_from_inline(ver["sample_data"]) if ver["sample_data"] else None
            ref = ver["catalogue_ref"] or {}
            entry = catalogue.by_key(ref.get("key") or "")
            if entry is not None:
                columns = [c.name for c in entry.columns]
            if not columns and sample:
                columns = sample.columns
    return DatasetDetailOut(
        **row,
        sample=sample,
        columns=columns,
        used_by_templates=await _template_uses(session, row["code"]),
    )


@router.get("/{uid}", name="get-dataset")
async def get_dataset(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> DatasetDetailOut:
    """One dataset's metadata (RLS-scoped), plus a preview for inline datasets + a column schema."""
    require_permission(principal, DATASETS_READ)
    # A PROVIDED entry is addressed by its catalogue key, because it has no row to hold a uid. The
    # key cannot collide with a firm's own uid (`_CODE_RE` forbids the dot), so this is checked
    # first and no stored dataset is ever shadowed by it.
    entry = catalogue.by_key(uid)
    if entry is not None:
        if entry.entitlement and not (
            is_system(principal) or satisfies(set(principal.permissions), entry.entitlement)
        ):
            raise HTTPException(403, f"Requires the '{entry.entitlement}' permission")
        return DatasetDetailOut(
            **_provided_out(entry).model_dump(),
            columns=[c.name for c in entry.columns],
            sample=None,
            used_by_templates=await _template_uses(session, entry.key),
        )
    return await _load_detail(session, uid)


@router.get("/{uid}/data", name="get-dataset-data")
async def get_dataset_data(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
    contact_id: int | None = Query(default=None, description="Subject for subject-scoped datasets"),
) -> DatasetDataOut:
    """Resolve the dataset now and return its rows (a live snapshot). A per-client dataset is
    resolved for ``contact_id``.

    The query runs with the **caller's own** claims (``authenticated`` + their claims, untouched by
    the resolver), so an adviser restricted to their book sees only their book here — a dataset is
    not a way around one's own scope. A dataset that can't resolve fails with the reason (422)
    instead of returning an empty grid, and a clipped result says so."""
    require_permission(principal, DATASETS_READ)
    code = (
        await session.execute(
            text("SELECT code FROM documents.datasets WHERE uid = :uid"), {"uid": uid}
        )
    ).scalar_one_or_none()
    # A PROVIDED CATALOGUE ENTRY IS NOT IN THE TABLE, and its uid IS its catalogue key (see
    # `DatasetSummary.uid`). Resolving one by uid therefore has to ask the registry too — the table
    # lookup alone 404'd every Graphene-provided dataset, so the chart panel could list a source and
    # its columns from the catalogue and then answer "Dataset not found" for its rows. The key is
    # checked against the registry, never used to build a query, so an unknown one still 404s.
    if code is None and catalogue.by_key(uid) is not None:
        code = uid
    if code is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    if isinstance(contact_id, int):
        await _assert_visible_contact(session, contact_id)
    tenant_id = await _tenant_id(session)
    resolution = await resolve_datasets_reporting(session, tenant_id, [code], subject_id=contact_id)
    blocking = resolution.blocking
    if blocking:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=blocking[0].detail
        )
    rows = resolution.rows.get(code, [])
    columns = list(rows[0].keys()) if rows else []
    truncated = any(p.reason == "truncated" for p in resolution.problems)
    # Every remaining (advisory) problem, not just the first — a run that came back both truncated
    # and partly empty should say both.
    notice = resolution.message()
    return DatasetDataOut(
        columns=columns,
        rows=[[r.get(c) for c in columns] for r in rows],
        truncated=truncated,
        notice=notice,
    )


def _output_schema(
    fmt: str, inline: DatasetSample | None, ref: CatalogueRef | None
) -> dict[str, Any]:
    """The column schema cached on ``documents.datasets.output_schema``, by format.

    Names only, deliberately: its one consumer is ``render/sample.py::synthesize_rows``, which
    lower-cases every member to guess a plausible value from the name. Storing richer entries here
    (a name plus a type) would raise there — the entry's typed columns are served to the picker
    through the list/detail endpoints instead.
    """
    if fmt == "inline" and inline is not None:
        cols: list[str] = list(inline.columns)
    elif fmt == "catalogue" and ref is not None:
        entry = catalogue.by_key(ref.key)
        cols = [c.name for c in entry.columns] if entry else []
    else:
        cols = []
    return {"columns": cols}


# ── authoring (datasets:write) ─────────────────────────────────────────────────
@router.post("", status_code=status.HTTP_201_CREATED, name="create-dataset")
async def create_dataset(
    body: DatasetCreate,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> DatasetDetailOut:
    """Create a dataset + its first published version."""
    require_permission(principal, DATASETS_WRITE)
    if not _CODE_RE.match(body.code):
        raise HTTPException(422, "code must be lower_snake_case (letters, digits, underscore)")
    # A firm's code must not collide with a shipped entry's key. A template binds a dataset by CODE,
    # so a firm row spelling `book_valuations` would shadow the provided entry of that name for
    # every template in the firm — and the two are resolved by different halves of
    # `_dataset_source`, so which one answered would depend on whether the row existed.
    if body.code.startswith(catalogue.RESERVED_KEY_PREFIX):
        raise HTTPException(
            422,
            f"Codes starting with {catalogue.RESERVED_KEY_PREFIX!r} are reserved for the datasets "
            "Graphene provides. Choose another code.",
        )
    if body.format not in _FORMATS:
        raise HTTPException(422, f"Unknown format {body.format!r}")
    if body.scope not in _SCOPES:
        raise HTTPException(
            422,
            "Scope must be 'global' or 'subject'"
            + (" — collection datasets aren't implemented." if body.scope == "collection" else "."),
        )
    _validate_source(body.format, body.inline, body.catalogue, body.scope, principal)
    # After the source checks on purpose: a bad setting is the more useful error, and the author has
    # to fix it either way. A valid firm-wide dataset then needs firm-wide entitlement.
    _require_firm_wide_author(principal, body.format, body.scope)
    dup = (
        await session.execute(
            text("SELECT 1 FROM documents.datasets WHERE code = :code"), {"code": body.code}
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A dataset with code {body.code!r} exists")
    uid = new_id()
    dataset_id = (
        await session.execute(
            text(
                "INSERT INTO documents.datasets "
                "  (uid, tenant_id, code, name, description, format, scope, "
                "   output_schema, created_by) "
                "VALUES (:uid, auth.tenant_id(), :code, :name, :desc, "
                "  CAST(:fmt AS documents.dataset_format), "
                "  CAST(:scope AS documents.dataset_scope), CAST(:schema AS jsonb), auth.uid()) "
                "RETURNING id"
            ),
            {
                "uid": uid,
                "code": body.code,
                "name": body.name,
                "desc": body.description,
                "fmt": body.format,
                "scope": body.scope,
                "schema": json.dumps(_output_schema(body.format, body.inline, body.catalogue)),
            },
        )
    ).scalar_one()
    await _insert_version(
        session, dataset_id, 1, body.format, body.inline, body.catalogue, body.sample
    )
    return await _load_detail(session, uid)


@router.patch("/{uid}", name="update-dataset")
async def update_dataset(
    uid: str,
    body: DatasetUpdate,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> DatasetDetailOut:
    """Update a dataset's metadata (name/description/enabled/scope). Format is immutable — a format
    change would mean a new dataset. A scope change is checked against the current source, so a
    table dataset can't be flipped to 'subject' at all and a configured provided dataset can't
    disagree with its entry's declared scope."""
    require_permission(principal, DATASETS_WRITE)
    current = (
        (
            await session.execute(
                text(
                    "SELECT d.id, d.format::text AS format, d.scope::text AS scope, "
                    "       v.catalogue_ref ->> 'key' AS catalogue_key "
                    "FROM documents.datasets d "
                    "LEFT JOIN LATERAL ("
                    "  SELECT catalogue_ref FROM documents.dataset_versions v "
                    "  WHERE v.dataset_id = d.id "
                    "  ORDER BY (v.status = 'published') DESC, v.version_number DESC LIMIT 1"
                    ") v ON true "
                    "WHERE d.uid = :uid"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    if body.scope is not None and body.scope != current["scope"]:
        if body.scope not in _SCOPES:
            raise HTTPException(
                422,
                "Scope must be 'global' or 'subject'"
                + (
                    " — collection datasets aren't implemented."
                    if body.scope == "collection"
                    else "."
                ),
            )
        # Flipping to 'global' IS declaring firm-wide data (it is resolved firm-wide at render),
        # so it takes the same entitlement as setting one up — checked before the coherence rules:
        # "you may not do this at all" beats "your dataset is shaped wrong".
        _require_firm_wide_author(principal, current["format"], body.scope)
        # scope ↔ source coherence: a table dataset can't be client-scoped at all…
        _assert_scope_supported(current["format"], body.scope)
        # …and a configured provided dataset takes its scope from the ENTRY, which is what actually
        # decides the claims the query runs under. Letting the row disagree is the escalation.
        if current["format"] == "catalogue":
            entry = catalogue.by_key(current["catalogue_key"] or "")
            if entry is not None and body.scope != entry.scope:
                raise HTTPException(
                    422,
                    f"{entry.name} is "
                    f"{'per-client' if entry.scope == 'subject' else 'firm-wide'}, so its scope "
                    f"must stay {entry.scope!r}.",
                )
    sets: list[str] = []
    params: dict[str, Any] = {"uid": uid}
    if body.name is not None:
        sets.append("name = :name")
        params["name"] = body.name
    if body.description is not None:
        sets.append("description = :desc")
        params["desc"] = body.description
    if body.enabled is not None:
        sets.append("enabled = :enabled")
        params["enabled"] = body.enabled
    # No `liveness` here on purpose: it is derived from the format (validated above), so there is
    # nothing to write — a knob that only ever agrees with the format isn't a knob.
    if body.scope is not None:
        sets.append("scope = CAST(:scope AS documents.dataset_scope)")
        params["scope"] = body.scope
    if sets:
        # `sets` are fixed "col = :bind" fragments (no user data interpolated); values are bound.
        clause = ", ".join(sets)
        stmt = f"UPDATE documents.datasets SET {clause} WHERE uid = :uid RETURNING id"  # noqa: S608
        updated = (await session.execute(text(stmt), params)).scalar_one_or_none()
        if updated is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    return await _load_detail(session, uid)


@router.delete("/{uid}", status_code=status.HTTP_204_NO_CONTENT, name="delete-dataset")
async def delete_dataset(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> None:
    """Delete a dataset (cascades to its versions).

    A **reference guard** refuses (409, with the count) while any live template declares this
    dataset's code: deleting it would leave those templates binding a code that no longer exists,
    which every future render would resolve to no rows — silently degrading client documents.
    Disable the dataset instead if it should stop being used but templates still reference it
    (mirrors ``forms.py::delete_form``)."""
    require_permission(principal, DATASETS_WRITE)
    row = (
        (
            await session.execute(
                text("SELECT id, code FROM documents.datasets WHERE uid = :uid"), {"uid": uid}
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    in_use = await _template_uses(session, row["code"])
    if in_use:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{in_use} template(s) still use this dataset. Unlink it from them first, or "
                "disable the dataset instead of deleting it."
            ),
        )
    await session.execute(text("DELETE FROM documents.datasets WHERE uid = :uid"), {"uid": uid})


@router.post("/{uid}/versions", status_code=status.HTTP_201_CREATED, name="add-dataset-version")
async def add_dataset_version(
    uid: str,
    body: DatasetVersionCreate,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> DatasetDetailOut:
    """Add a published version carrying the source the dataset's (immutable) format requires."""
    require_permission(principal, DATASETS_WRITE)
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, format::text AS format, scope::text AS scope "
                    "FROM documents.datasets WHERE uid = :uid"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    _validate_source(row["format"], body.inline, body.catalogue, row["scope"], principal)
    # Republishing an existing firm-wide dataset is declaring firm-wide data just as much as
    # setting one up — without this, a book-scoped author could re-point a colleague's firm-wide
    # dataset and read the result out of the next generated document.
    _require_firm_wide_author(principal, row["format"], row["scope"])
    next_vn = (
        await session.execute(
            text(
                "SELECT COALESCE(max(version_number), 0) + 1 FROM documents.dataset_versions "
                "WHERE dataset_id = :did"
            ),
            {"did": row["id"]},
        )
    ).scalar_one()
    await _insert_version(
        session, row["id"], int(next_vn), row["format"], body.inline, body.catalogue, body.sample
    )
    # The source changed, so refresh the cached column schema the editor preview reads.
    await session.execute(
        text("UPDATE documents.datasets SET output_schema = CAST(:s AS jsonb) WHERE id = :id"),
        {
            "s": json.dumps(_output_schema(row["format"], body.inline, body.catalogue)),
            "id": row["id"],
        },
    )
    return await _load_detail(session, uid)


@router.get("/{uid}/versions", name="list-dataset-versions")
async def list_dataset_versions(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[DatasetVersionOut]:
    """A dataset's version history, newest first — each data edit publishes a new version."""
    require_permission(principal, DATASETS_READ)
    ds = (
        await session.execute(
            text("SELECT id FROM documents.datasets WHERE uid = :uid"), {"uid": uid}
        )
    ).scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    rows = (
        (
            await session.execute(
                text(
                    "SELECT v.uid, v.version_number, v.status::text AS status, v.created_at, "
                    "       v.published_at, v.catalogue_ref, v.inline_data, "
                    "       u.name AS created_by "
                    "FROM documents.dataset_versions v "
                    "LEFT JOIN auth.users u ON u.id = v.created_by "
                    "WHERE v.dataset_id = :did "
                    "ORDER BY v.version_number DESC"
                ),
                {"did": ds},
            )
        )
        .mappings()
        .all()
    )
    return [
        DatasetVersionOut(
            uid=r["uid"],
            version_number=r["version_number"],
            status=r["status"],
            created_at=r["created_at"],
            published_at=r["published_at"],
            created_by=r["created_by"],
            inline=_sample_from_inline(r["inline_data"]),
            catalogue=(
                CatalogueRef(
                    key=(r["catalogue_ref"] or {}).get("key", ""),
                    params=(r["catalogue_ref"] or {}).get("params") or {},
                )
                if r["catalogue_ref"]
                else None
            ),
        )
        for r in rows
    ]
