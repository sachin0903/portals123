"""List-endpoint extractors. Seccl has no updated-since filters on these, so
each pull is necessarily the full list — but ``full_merge`` applies it as a SQL
set-difference diff (insert new/changed, delete changed/vanished keys), so an
unchanged entity writes nothing. Webhooks cover intraday changes for the
entities that have them; these schedules are the reconciliation baseline."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa
from interlace import model

from seccl_sync.client import SecclClient
from seccl_sync.records import records_to_table

_ID_SPINE: dict[str, pa.DataType] = {"id": pa.string()}


@dataclass(frozen=True)
class EntitySpec:
    name: str
    path: str  # "{firm}" is replaced with the configured firm id
    cron: str
    description: str
    strategy: str = "full_merge"
    key: tuple[str, ...] = ("id",)
    paginated: bool = True
    params: dict[str, Any] = field(default_factory=dict)
    stamp_firm: bool = False  # add firm_id when the payload doesn't carry it
    # Rows per page. 100 is what the previous integration ran against production
    # for years: these entities embed nested arrays (an account carries its whole
    # positions[]), and production 500s outright on a 500-row page of them
    # (verified live: GET /account/list/SELCT?pageSize=500 -> 500). None = the
    # client default, for the light endpoints.
    page_size: int | None = 100

    def request_params(self) -> dict[str, Any]:
        """Params for the pull, with the entity's page size folded in.

        Setting ``pageSize`` explicitly also opts the entity out of ``get_list``'s
        refetch-as-one-exactly-sized-page shortcut — necessary here, because these
        payloads are what makes the endpoint fall over in the first place.
        """
        params = dict(self.params)
        if self.page_size is not None:
            params.setdefault("pageSize", self.page_size)
        return params


LIST_ENTITIES: tuple[EntitySpec, ...] = (
    EntitySpec("raw.nodes", "/firm/node/list/{firm}", "0 2 * * *", "Firm nodes (hierarchy)"),
    EntitySpec("raw.users", "/user/{firm}", "0 2 * * *", "Firm/DFM users"),
    EntitySpec(
        "raw.illustrations",
        "/illustration/product/{firm}",
        "0 3 * * *",
        "Product illustrations",
        params={"pageSize": 10},
    ),
    # These four look unpaginated (the server returns the full set when no
    # pageSize is sent) but they DO return meta.count — treat them as list
    # endpoints so a server-side page cap can never silently truncate them
    # (masterparties is 1,600+ rows).
    EntitySpec("raw.models", "/model/{firm}", "0 2 * * *", "Investment models"),
    EntitySpec("raw.client_products", "/clientProduct/{firm}", "0 2 * * *", "Client products"),
    # Covers BOTH transfer directions, which is why /masterparty/transferoutprovider is not
    # synced. Verified against staging: master_parties holds 1,612 rows, of which 661 carry
    # `transfer_providers` (can transfer IN) and 7 carry `transfer_out_provider` (can transfer
    # OUT, as {cashSupported, inSpecieSupported}). The transferoutprovider endpoint returns a
    # strict subset -- 5 rows, being those 7 minus the two whose cashSupported is false, because
    # it filters on the mandatory `productTransferType=Cash` query parameter. Every field it
    # returns is already here, so syncing it would be a second call for a WHERE clause.
    EntitySpec(
        "raw.master_parties",
        "/masterparty/platform/{firm}",
        "0 4 * * 1",
        "Ceding providers / master parties (both transfer directions)",
    ),
    EntitySpec(
        "raw.nationalities",
        "/static/nationality",
        "0 4 * * 1",
        "Nationality static data",
        paginated=False,
        page_size=None,
    ),
    EntitySpec(
        "raw.transaction_codes",
        "/static/transactiontype",
        "0 4 * * 1",
        "Transaction-type static data",
        # `id` is NOT unique here (BUY/SELL repeat across sub-type x movement
        # variants — verified live); the triple is the natural key.
        key=("id", "transaction_sub_type", "movement_type"),
        paginated=False,
        page_size=None,
    ),
    EntitySpec("raw.assets", "/asset/{firm}", "0 2 * * *", "Firm asset universe"),
    EntitySpec("raw.portfolios", "/portfolio/{firm}", "0 2 * * *", "Portfolios"),
    EntitySpec(
        "raw.dashboard_information",
        "/dashboard/information/{firm}",
        "30 5 * * 2-6",
        "Dashboard information snapshot",
        strategy="replace",
        key=(),
        paginated=False,
        page_size=None,
        stamp_firm=True,
    ),
    EntitySpec(
        "raw.dashboard_exceptions",
        "/dashboard/exception/{firm}",
        "30 5 * * 2-6",
        "Dashboard exception counters",
        strategy="replace",
        key=(),
        paginated=False,
        page_size=None,
        stamp_firm=True,
    ),
)


def _extractor(spec: EntitySpec) -> Callable[[], Coroutine[Any, Any, pa.Table]]:
    async def pull() -> pa.Table:
        async with SecclClient() as api:
            path = spec.path.format(firm=api.firm_id)
            if spec.paginated:
                records = await api.get_list(path, spec.request_params())
            else:
                records = await api.get_records(path, spec.request_params() or None)
            if spec.stamp_firm:
                for record in records:
                    record.setdefault("firmId", api.firm_id)
        if not records and spec.strategy == "full_merge":
            # a full_merge over an empty source would diff-delete the whole table;
            # an empty listing here is almost certainly an API fault, so fail the run
            raise RuntimeError(
                f"{spec.name}: empty listing from {path}; refusing to delete all rows"
            )
        return records_to_table(records, spine=_ID_SPINE)

    pull.__name__ = spec.name
    return pull


for _spec in LIST_ENTITIES:
    model(
        name=_spec.name,
        strategy=_spec.strategy,
        key=_spec.key,
        schedule={"cron": _spec.cron},
        tags=("seccl", "raw"),
        description=_spec.description,
    )(_extractor(_spec))


# The client list, written out for the same reason the fee structures below are: it needs a spine
# of its own, and widening `_extractor`/`EntitySpec` to carry one re-fingerprints every entity
# that shares them, so `plan` marks each one `breaking / rebuild` and a deploy re-pulls the
# whole firm to arrive at byte-identical tables.
#
# THE SPINE IS THE READ SHAPE, not just the key -- every column `models/platform/clients.sql`
# names. `records_to_table` infers columns from the records it is given, so a field NO client in
# the firm carries is not a column at all, and the conformed model then fails to BIND:
# `Binder Error: Values list "c" does not have a column named "..."`, which aborts the entity and
# lands in the merge ledger as an unavailable slice -- indistinguishable from "this firm has no
# clients". It is not hypothetical: `phone` did exactly that. It is a real field -- it is in the
# client shape the v1 pipeline landed -- but a rarely populated one: no client in this firm's book
# carries a landline, and neither does any of the 205 example bodies in the vendored Postman
# collection, which is why it reads as a field that does not exist right up until a firm has one.
# `mobile` is one firm of clients who gave no number away from the same failure, `entity_name` one
# firm of sole individuals, `middle_name` one firm of people with two names -- and each of those
# is likeliest on a SMALL firm, i.e. on a new client's first sync.
#
# Interlace's own schema evolution does not cover this and is not meant to: it is additive on the
# WRITE side (`plan/apply.py::_align_stage_to_target` grows the target to fit what the model
# produced), so it answers "a new field appeared", where this is "a field never appeared and
# something reads it". Only a declaration can answer that.
#
# The spine stops at what the SQL names, and the tripwire in tests/test_platform_models.py keeps
# it there. The payload is far wider -- gender, employers, kyc_data, vulnerability, marital_status
# and some thirty more land beside these -- and those columns are better left inferred: a spine
# entry pins a TYPE as well as guaranteeing presence, and `records_to_table` widens a value it
# cannot cast to the declared type into a STRING, so declaring a boolean's column here as text
# would land "true" where a reader expects true. Add a field when a model reads it, not before.
#
# Every column is declared VARCHAR because that is what lands: `records_to_table` serialises
# anything nested to a JSON string, and the scalar-or-list `node_id` widens to string anyway. The
# spine is a floor, not a projection -- whatever else the payload carries still lands beside it,
# and the whole record survives in the conformed `data` column either way.
_CLIENT_SPINE: dict[str, pa.DataType] = {
    "id": pa.string(),
    "node_id": pa.string(),
    "client_type": pa.string(),
    "first_name": pa.string(),
    "middle_name": pa.string(),
    "surname": pa.string(),
    "entity_name": pa.string(),
    "title": pa.string(),
    "date_of_birth": pa.string(),
    "nationalities": pa.string(),
    "nationality": pa.string(),
    "tax_domicile": pa.string(),
    "national_insurance_no": pa.string(),
    "email": pa.string(),
    "mobile": pa.string(),
    "phone": pa.string(),
    "address_detail": pa.string(),
    "status": pa.string(),
    "record_creation_date": pa.string(),
    "audit_details": pa.string(),
}


@model(
    name="raw.clients",
    strategy="full_merge",
    key="id",
    schedule={"cron": "0 2 * * *"},
    tags=("seccl", "raw"),
    description="Clients (webhook covers intraday)",
)
async def raw_clients() -> pa.Table:
    async with SecclClient() as api:
        path = f"/client/{api.firm_id}"
        # The page size the list entities send, and sent for the same second reason: an explicit
        # `pageSize` opts out of `get_list`'s refetch-as-one-exactly-sized-page shortcut.
        records = await api.get_list(path, {"pageSize": 100})
    if not records:
        # The guard every full_merge list entity carries: an empty source diff-deletes the whole
        # table, and a firm with no clients at all is an API fault rather than a fact.
        raise RuntimeError(f"raw.clients: empty listing from {path}; refusing to delete all rows")
    return records_to_table(records, spine=_CLIENT_SPINE)


# Third-party scheme products, written out for the same reason as the client list. The shared
# extractor's spine is only `id`, and `platform.third_party_providers` names every column below.
# A field no row in the firm carries is not a column, and the conformed model then fails to bind
# (`external_system` does this on production: staging sends it, production does not). Widening
# `_extractor` would re-fingerprint every list entity.
_THIRD_PARTY_SPINE: dict[str, pa.DataType] = {
    "id": pa.string(),
    "available_to_node_id": pa.string(),
    "product_name": pa.string(),
    "scheme_name": pa.string(),
    "wrapper_type": pa.string(),
    "contact_name": pa.string(),
    "contact_number": pa.string(),
    "tax_wrapper_administrator": pa.string(),
    "pension_scheme_tax_reference": pa.string(),
    "scheme_fca_reference_number": pa.string(),
    "relief_at_source_reference_number": pa.string(),
    "origo_identifiers": pa.string(),
    "external_system": pa.string(),
    "audit_details": pa.string(),
}


@model(
    name="raw.third_party_products",
    strategy="full_merge",
    key="id",
    schedule={"cron": "0 3 * * *"},
    tags=("seccl", "raw"),
    description="Third-party scheme products",
)
async def raw_third_party_products() -> pa.Table:
    async with SecclClient() as api:
        path = f"/schemeProduct/{api.firm_id}"
        records = await api.get_list(path, {"pageSize": 100})
    if not records:
        raise RuntimeError(
            f"raw.third_party_products: empty listing from {path}; refusing to delete all rows"
        )
    return records_to_table(records, spine=_THIRD_PARTY_SPINE)


# The account list, written out rather than left on the shared `_extractor`: widening that
# function re-fingerprints every list entity. `portfolio_groups` is kept on the spine so a
# list that starts sending the field still has a typed column. platform.valuations does not
# read it from here — the group arrives on the account detail, and that extract's spine is
# what keeps the column present when a book of GIAs never sends it.
_ACCOUNT_SPINE: dict[str, pa.DataType] = {
    "id": pa.string(),
    "portfolio_groups": pa.string(),
}


@model(
    name="raw.accounts",
    strategy="full_merge",
    key="id",
    schedule={"cron": "0 2 * * *"},
    tags=("seccl", "raw"),
    description="Account summaries + positions",
)
async def raw_accounts() -> pa.Table:
    async with SecclClient() as api:
        path = f"/account/list/{api.firm_id}"
        records = await api.get_list(path, {"pageSize": 100})
    if not records:
        raise RuntimeError(f"raw.accounts: empty listing from {path}; refusing to delete all rows")
    return records_to_table(records, spine=_ACCOUNT_SPINE)


# The firm's priced fee structures -- what platform.fee_packages is built from.
#
# WRITTEN OUT RATHER THAN ADDED TO LIST_ENTITIES, and not for taste: interlace fingerprints a
# Python model from `inspect.getsource(fn)` alone, and every entity above shares `_extractor`.
# Widening that function or EntitySpec to carry this endpoint's two peculiarities re-fingerprints
# every one of them, so `plan` marks each `breaking / rebuild` and a deploy re-pulls the whole
# firm -- 1,612 master parties, 13k transactions and three 376-call per-account fan-outs -- to
# arrive at byte-identical tables. (docs/sei.md documents the same fingerprinting rule from the
# other side: a shared extractor hides real read-logic changes from `plan`.)
#
# The two peculiarities:
#
# 1. THE `/v1/` PREFIX. This is the only endpoint in the sync carrying a version segment; the
#    rest sit at the API root.
#
# 2. AN EMPTY LISTING IS LEGITIMATE, so there is no "refusing to delete all rows" guard. Verified
#    live against staging (04 Aug 2026): SELCT answers `{"data": [], "meta": {"count": 0, "page":
#    1, "pageSize": 20}}` -- a well-formed response from a firm that prices by hand. The guard the
#    other entities carry would fail this model on every run for such a firm. `full_merge` is
#    still right: a fee structure the firm has deleted must stop being offered, where
#    merge would leave it in the register to be priced against forever.
#
# The spine is the whole read shape, not just the key, for the same reason: with no rows to infer
# from, `type`/`tiers` would not exist as columns and platform/fee_packages.sql would fail to bind
# on precisely the firm that has no fee structures.
_FEE_SPINE: dict[str, pa.DataType] = {
    "id": pa.string(),
    "firm_id": pa.string(),
    "type": pa.string(),
    "name": pa.string(),
    # Nested, so a JSON string by the records convention.
    "tiers": pa.string(),
    "excluded_wrapper_types": pa.string(),
}


@model(
    name="raw.fee_structures",
    strategy="full_merge",
    key="id",
    schedule={"cron": "0 2 * * *"},
    tags=("seccl", "raw"),
    description="Priced fee structures (platform + adviser charges)",
)
async def raw_fee_structures() -> pa.Table:
    async with SecclClient() as api:
        records = await api.get_list(f"/v1/fee-structures/{api.firm_id}")
        # The payload names no firm, and platform.fee_packages scopes by the firm's root org.
        for record in records:
            record.setdefault("firmId", api.firm_id)
    return records_to_table(records, spine=_FEE_SPINE)
