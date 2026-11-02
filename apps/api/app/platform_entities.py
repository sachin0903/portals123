"""A firm's slice of a platform's entities, as searchable tables.

Admin > Platforms > {provider} > Firms > {uid} > PLATFORM. Answers "what does this custodian
hold for this firm" -- the question `/platforms/{slug}` never could, because that page shows the
PIPELINE (models, runs, lineage) and not the book.

Reads the provider's core warehouse through `app.lake`'s read-only attach, narrowed to the one
firm by its slice key. Morgan Lloyd's is `ml_tenant_id`: one Dynamics/Azure tenant per firm, the
discriminator every raw model stamps on every record.

THREE RULES, in the order they matter.

**No identifier from a request reaches the SQL.** Relation names, column names and the sort
column all come from the registry below; the caller's `q` and paging are bound parameters. This
is the posture `platforms._query_sync` already takes for the SQL console, and it matters more
here because these paths are assembled rather than typed by an operator.

**Declared columns are INTERSECTED with the relation, never assumed.** `raw.contacts` has 459
columns and `raw.transfers` 538; the registry names the twenty worth showing (the vendor
documents them -- see `morgan_lloyd.manifest`), and any that the relation does not actually have
are dropped and REPORTED rather than crashing the query. A vendor renaming a field should cost
one missing column and a visible note, not a dead page.

**An absent relation is not an empty one.** A model that has never run, a catalogue that was
reset, and a firm with genuinely no data are three different facts that all render as zero rows.
Each entity reports its own `availability`, and the UI states the reason.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any

from app.ids import tenant_model_key
from app.listing import DEFAULT_LIMIT
from app.platforms import PlatformUnavailable, _owned_core
from app.providers import promoted_relation

logger = logging.getLogger(__name__)

_MAX_LIMIT = 200


@dataclass(frozen=True, slots=True)
class Join:
    """A registry-declared lookup to another relation, so a table can show a NAME.

    Dataverse stores a relationship as `_thing_value`, a bare GUID, and a column of GUIDs answers
    nothing a person asked. `raw.schemes.cam_individualclient_value` is the client the scheme is
    for; what an operator wants to read is "Sophie Halloway".

    Every part is a registry literal -- no caller value reaches the SQL -- and the join is DROPPED
    rather than fatal when the other relation has not been built, so one unbuilt model costs a
    column and not the page.
    """

    column: str
    """The local foreign key, e.g. `cam_individualclient_value`."""
    relation: str
    """What it points at, e.g. `raw.contacts`."""
    key: str
    """The other relation's primary key, e.g. `contactid`."""
    label: tuple[str, ...]
    """Columns joined with a space to name the row. Blanks are skipped."""
    alias: str
    """The output column's name. Appears in `columns` like any other."""


@dataclass(frozen=True, slots=True)
class EntityDef:
    """One tab of the firm console."""

    key: str
    label: str
    relation: str
    """The warehouse relation, in the provider's consumption namespace."""
    columns: tuple[str, ...]
    """Preferred display order. Intersected with the relation at query time."""
    search: tuple[str, ...] = ()
    """Columns the free-text box matches, ILIKE. Text columns only -- a cast of a 459-column
    row to text per predicate is what makes a search feel broken."""
    order_by: str | None = None
    descending: bool = True
    unavailable: str = ""
    """Non-empty when there is no source at all. Stated, never rendered as an empty table."""
    slice_column: str | None = "ml_tenant_id"
    """The per-firm discriminator. None for a relation that is not firm-scoped."""
    note: str = ""
    source: str = "lake"
    """Where the rows come from: `lake` (the provider's warehouse) or `outbound` (our own record
    of what we SENT, `audit.api_log`).

    An outbound entity is one the vendor does not publish back, so the lake can never hold it --
    valuations are 403 for our service principal, and documents and illustrations have no
    collection form at all. Its rows are the calls we made, which is not a lesser answer: it is
    the only complete one, because it includes the attempts that FAILED, and a projection of
    vendor data by definition cannot.
    """
    operations: tuple[str, ...] = ()
    """For an outbound entity, the vendor operations whose calls constitute it.

    A closed list, matched exactly. Prefix matching would fold `cam_createwrapper` and a future
    `cam_createwrapperbulk` into one tab silently.
    """
    lookup_value: str = ""
    """The column an action form must SEND when this entity is picked from.

    Not always the primary key: `cam_createbeneficiaries` and friends take a `schemeReference`,
    which is `raw.schemes.cam_name` (`MLSIPP/01417`) and not `cam_schemeid`. Getting this wrong
    is the `cam_schemerefs` mistake again -- a plausible identifier the vendor refuses.
    """
    lookup_label: tuple[str, ...] = ()
    """Columns joined with a space to name the row for a human. Blank ones are skipped, so a
    contact with no surname reads as their first name rather than as "Ada  "."""
    lookup_hint: str = ""
    """A second, dimmer line -- an email, a status. What tells two "J Smith"s apart."""
    joins: tuple[Join, ...] = ()
    """Related names to resolve, so a column of GUIDs becomes a column of people."""


# An outbound entity's shape is the LOG's, not the vendor's, and it is the same for all of them
# -- the question is always "when, who, what did we send it about, and what came back". Named once
# so the three tabs cannot drift into three different answers to the same question.
_OUTBOUND_RELATION = "audit.api_log"
_OUTBOUND_COLUMNS = (
    "created_at",
    "operation",
    "entity_id",
    "ok",
    "status_code",
    "error",
    "duration_ms",
    "actor",
)
# `error` is searchable ON PURPOSE. The reason to open one of these tabs is usually that
# something did not arrive, and "show me every push that mentions this scheme reference" is the
# question -- which for a failure lives in the vendor's message, not in a column.
_OUTBOUND_SEARCH = ("operation", "error", "actor")

# Morgan Lloyd. Columns are the documented ones (`morgan_lloyd.manifest`) where the vendor's
# request-field names ARE the Dataverse column names -- true throughout `raw.contacts`, which is
# why Create Contact's field list reads across almost exactly -- and otherwise the identifying
# and status columns of the entity, checked against the live relation.
_MORGAN_LLOYD: tuple[EntityDef, ...] = (
    EntityDef(
        key="contacts",
        label="Contacts",
        lookup_value="contactid",
        lookup_label=("firstname", "lastname"),
        lookup_hint="emailaddress1",
        relation="raw.contacts",
        columns=(
            "contactid",
            "firstname",
            "lastname",
            "emailaddress1",
            "cam_ninumber",
            "cam_birthdate",
            "gendercode",
            "cam_nationality",
            "address1_city",
            "address1_postalcode",
            "telephone1",
            "mobilephone",
            "familystatuscode",
            "cam_employmentstatus",
            "cam_retirementageint",
            "cam_annualearnings",
            "cam_potentiallyvulnerable",
            "statuscode",
            "modifiedon",
        ),
        search=("firstname", "lastname", "emailaddress1", "cam_ninumber"),
        order_by="modifiedon",
        note="459 columns are synced; these are the ones Create Contact documents.",
    ),
    EntityDef(
        key="schemes",
        label="Schemes",
        lookup_value="cam_name",
        lookup_label=("cam_name",),
        lookup_hint="statuscode",
        relation="raw.schemes",
        # Chosen by reading the 313 columns and keeping what actually holds something. The first
        # cut showed the reference THREE TIMES -- `cam_name`, `cam_schemeref` (identical) and
        # `cam_schemereference` (null on every row, so a column of dashes) -- and none of the
        # facts an operator wants. `cam_name` is the one kept because it is the value the scoped
        # actions are proven to accept.
        joins=(
            Join(
                column="cam_individualclient_value",
                relation="raw.contacts",
                key="contactid",
                label=("firstname", "lastname"),
                alias="client",
            ),
        ),
        columns=(
            "cam_name",
            "client",
            "cam_individualclient_value",
            "as_schemetype",
            "cam_dsipptype",
            "cam_crystallisedfundamount",
            "cam_uncrystallisedfundamount",
            "cam_beneficiaryfund",
            "cam_ongoingadvisorfeeapplicable",
            "cam_feearrearsapplicable",
            "cam_pstrnumber",
            "statuscode",
            "createdon",
            "modifiedon",
        ),
        search=("cam_name", "client", "cam_pstrnumber"),
        order_by="modifiedon",
        note=(
            "`cam_name` is the scheme reference (MLSIPP/01417) the scoped actions take. "
            "No benefit-application column exists on the scheme — the id is a create-time input "
            "only. Fee DETAIL is absent for a reason: 13 of the entity's 42 fee columns are "
            "ML-Team field-secured, so they read as empty for our service principal, and only "
            "the two applicability flags come back."
        ),
    ),
    EntityDef(
        key="advisers",
        label="Advisers",
        lookup_value="contactid",
        lookup_label=("adviser_email",),
        relation="raw.adviser_contacts",
        columns=("contactid", "adviser_email"),
        search=("adviser_email",),
        order_by=None,
        note=(
            "Enumerated from the Financial Adviser Firm (MORGAN_LLOYD_ADVISER_FIRM_ID) — the "
            "same GUID the vendor issues as Create Investment's `provider`, not the Microsoft "
            "tenant id. Empty without it, and empty until the next sync after setting it."
        ),
    ),
    EntityDef(
        key="beneficiaries",
        label="Beneficiaries",
        lookup_value="cam_nominationofbeneficiaryid",
        lookup_label=("cam_name",),
        lookup_hint="cam_percentageoffund",
        relation="raw.beneficiaries",
        columns=(
            "cam_nominationofbeneficiaryid",
            "cam_name",
            "cam_percentageoffund",
            "cam_nominationdate",
            "cam_city",
            "modifiedon",
        ),
        search=("cam_name",),
        order_by="modifiedon",
    ),
    EntityDef(
        key="transfers",
        label="Transfers",
        lookup_value="cam_personalpolicyid",
        lookup_label=("cam_name",),
        lookup_hint="cam_transferstatus",
        relation="raw.transfers",
        columns=(
            "cam_personalpolicyid",
            "cam_name",
            "cam_transferstatus",
            "statuscode",
            "modifiedon",
        ),
        search=("cam_name",),
        order_by="modifiedon",
        note="`cam_personalpolicies` is the transfers entity set.",
    ),
    EntityDef(
        key="contributions",
        label="Contributions",
        lookup_value="cam_contributionid",
        lookup_label=("cam_name",),
        lookup_hint="cam_amount",
        relation="raw.contributions",
        columns=(
            "cam_contributionid",
            "cam_name",
            "cam_amount",
            "cam_paymenttype",
            "cam_status",
            "modifiedon",
        ),
        search=("cam_name",),
        order_by="modifiedon",
        note="Contribution APPLICATIONS. Received contributions are a separate collection.",
    ),
    EntityDef(
        key="received_contributions",
        label="Received contributions",
        relation="raw.pension_contributions",
        columns=("cam_pensioncontributionid", "cam_name", "modifiedon"),
        search=("cam_name",),
        order_by="modifiedon",
    ),
    EntityDef(
        key="benefits",
        label="Benefit applications",
        lookup_value="cam_benefitsapplicationid",
        lookup_label=("cam_name",),
        lookup_hint="statuscode",
        relation="raw.benefit_applications",
        columns=(
            "cam_benefitsapplicationid",
            "cam_name",
            "cam_amount",
            "cam_incomefrequency",
            "statuscode",
            "modifiedon",
        ),
        search=("cam_name",),
        order_by="modifiedon",
    ),
    EntityDef(
        key="bank_transactions",
        label="Bank transactions",
        relation="raw.bank_transactions",
        columns=("financial_transaction_name", "scheme_ref"),
        search=("financial_transaction_name", "scheme_ref"),
        order_by=None,
        note=(
            "cam_getaccounttransactions, per scheme. A scheme with none answers "
            '"No Financial Transactions were found", which is an empty result, not a failure.'
        ),
    ),
    # --- the outbound four -------------------------------------------------------------------
    #
    # These are things we SEND, not things Morgan Lloyd publishes, so their tab shows the push
    # LEDGER -- one row per attempt with its outcome -- rather than a projection of vendor data.
    # Three of them cannot be read back at all: the service principal is denied
    # `cam_policyvaluations` and `cam_assetvaluations` (403, a permissions request to Clifton,
    # not a code gap), and documents and illustrations have no collection form. The ledger is
    # the only honest answer to "what did we send", and it is also the only possible one.
    EntityDef(
        key="valuations",
        label="Valuations",
        relation="sink.daily_valuations",
        columns=("scheme_reference", "valuation_date", "value", "status", "detail"),
        search=("scheme_reference", "status"),
        order_by="valuation_date",
        note=(
            "Outbound push ledger. Reading valuations BACK is 403 for our service principal "
            "(cam_policyvaluations / cam_assetvaluations)."
        ),
    ),
    EntityDef(
        key="investments",
        label="Investments",
        relation=_OUTBOUND_RELATION,
        source="outbound",
        operations=("cam_createwrapper",),
        columns=_OUTBOUND_COLUMNS,
        search=_OUTBOUND_SEARCH,
        order_by="created_at",
        slice_column=None,
        note='Every cam_createwrapper we sent. The doc titles this action "Create Investment"; '
        "there is no cam_createinvestment in the org.",
    ),
    EntityDef(
        key="documents",
        label="Documents",
        relation=_OUTBOUND_RELATION,
        source="outbound",
        operations=("cam_submitdocument",),
        columns=_OUTBOUND_COLUMNS,
        search=_OUTBOUND_SEARCH,
        order_by="created_at",
        slice_column=None,
        note="Every cam_submitdocument we sent. No collection form exists to read documents "
        "back, so this is the whole record.",
    ),
    EntityDef(
        key="illustrations",
        label="Illustrations",
        relation=_OUTBOUND_RELATION,
        source="outbound",
        # BOTH legs. CTC produces the illustration (service 'ctc', recorded under provider
        # 'morgan-lloyd' because a firm thinks of it as Morgan Lloyd producing their document),
        # and cam_illustration attaches it to the scheme. Showing only the second would hide
        # every illustration that failed before there was anything to attach.
        operations=("cam_illustration", "run_quote"),
        columns=_OUTBOUND_COLUMNS,
        search=_OUTBOUND_SEARCH,
        order_by="created_at",
        slice_column=None,
        note="Every illustration call — CTC producing it and cam_illustration attaching it.",
    ),
)

# Seccl and SEI, from `tenant_{uid}.*` -- the firm's OWN slice, computed by the provider's chain
# (docs/provider-pipeline.md) rather than filtered again here. Two reasons that beats a predicate:
# the console and the pipeline cannot disagree about what a firm's slice is, and the conformed
# columns (docs/platform-model.md §3) are the same words for both custodians, so the tabs read
# alike where Morgan Lloyd's necessarily read in Dataverse's vocabulary.
#
# `slice_column=None` throughout: the schema IS the narrowing. Adding an org filter on top would
# be a second, weaker copy of a decision already made.
_CONFORMED_SPINE = ("external_id", "key")


def _conformed(
    key: str,
    label: str,
    entity: str,
    columns: tuple[str, ...],
    *,
    search: tuple[str, ...] = (),
    order_by: str | None = "as_of_date",
    lookup_value: str = "external_id",
    lookup_label: tuple[str, ...] = (),
    lookup_hint: str = "",
    note: str = "",
) -> EntityDef:
    """One conformed entity from a provider's per-tenant schema.

    A helper because the shape repeats seventeen times and the only thing that varies is which
    columns are worth showing -- and because `relation` must carry the `{uid}` placeholder every
    time, which is exactly the sort of thing that gets forgotten once and produces a tab reading
    another firm's book.
    """
    # The sort column is SHOWN, always. `order_by` is validated against the projection, so an
    # entity that sorts by a column it does not display silently does not sort at all -- six of
    # these did, and a table that quietly ignores its own ordering is the kind of wrong nobody
    # reports. Appending here rather than in each declaration means it cannot be forgotten again.
    if order_by and order_by not in columns:
        columns = (*columns, order_by)
    return EntityDef(
        key=key,
        label=label,
        relation=f"tenant_{{uid}}.{entity}",
        columns=columns,
        search=search,
        order_by=order_by,
        slice_column=None,
        lookup_value=lookup_value,
        lookup_label=lookup_label or (lookup_value,),
        lookup_hint=lookup_hint,
        note=note,
    )


_SECCL: tuple[EntityDef, ...] = (
    _conformed(
        "clients",
        "Clients",
        "clients",
        (
            "external_id",
            "client_type",
            "first_name",
            "surname",
            "entity_name",
            "date_of_birth",
            "email",
            "status",
            "key",
            "as_of_date",
        ),
        search=("external_id", "first_name", "surname", "entity_name", "email"),
        lookup_label=("first_name", "surname"),
        lookup_hint="email",
    ),
    _conformed(
        "accounts",
        "Accounts",
        "accounts",
        (
            "external_id",
            "account_name",
            "product_key",
            "status",
            "is_discretionary",
            "is_advised",
            "base_currency",
            "key",
            "as_of_date",
        ),
        search=("external_id", "account_name", "product_key"),
        lookup_label=("account_name",),
        lookup_hint="product_key",
    ),
    _conformed(
        "transactions",
        "Transactions",
        "transactions",
        (
            "external_id",
            "account_external_id",
            "type",
            "movement_type",
            "isin",
            "asset_name",
            "as_of_date",
        ),
        search=("external_id", "account_external_id", "isin", "asset_name"),
    ),
    _conformed(
        "positions",
        "Positions",
        "positions",
        (
            "external_id",
            "account_external_id",
            "asset_name",
            "isin",
            "quantity",
            "price",
            "market_value",
            "currency",
        ),
        search=("account_external_id", "asset_name", "isin"),
    ),
    _conformed(
        "valuations",
        "Valuations",
        "valuations",
        (
            "external_id",
            "account_external_id",
            "as_of_date",
            "market_value",
            "cash_value",
            "book_cost",
            "unrealised_gain",
            "currency",
        ),
        search=("account_external_id",),
    ),
    _conformed(
        "instruments",
        "Instruments",
        "instruments",
        ("external_id", "name", "asset_type", "isin", "sedol", "currency", "exchange_code"),
        search=("name", "isin", "sedol"),
        lookup_label=("name",),
        lookup_hint="isin",
    ),
    _conformed(
        "models",
        "Models",
        "models",
        (
            "external_id",
            "name",
            "model_type",
            "risk_rating",
            "product_key",
            "model_charge",
            "decision_maker",
        ),
        search=("name", "product_key"),
        lookup_label=("name",),
    ),
    _conformed(
        "nodes",
        "Organisations",
        "nodes",
        ("external_id", "name", "type", "parent_key", "firm_key", "depth", "as_of_date"),
        search=("name", "external_id"),
        lookup_label=("name",),
        lookup_hint="type",
        note="Seccl's node tree. `key`/`lineage` are what a firm's slice is keyed on.",
    ),
    _conformed(
        "fee_packages",
        "Fee packages",
        "fee_packages",
        ("external_id", "name", "category", "fee_type", "frequency", "charge_basis", "rule"),
        search=("name",),
        note="Firm-wide (empty `lineage`): scoping a fee register to the firm ROOT reads as "
        "tighter and is wrong, because containment tests ancestry.",
    ),
    _conformed(
        "third_party_providers",
        "Third-party providers",
        "third_party_providers",
        (
            "external_id",
            "name",
            "scheme_name",
            "product_key",
            "contact_name",
            "pension_scheme_tax_reference",
        ),
        search=("name", "scheme_name"),
    ),
    _conformed(
        "transfer_providers",
        "Transfer providers",
        "transfer_providers",
        ("external_id", "name", "status", "is_active", "products", "transfers_in", "transfers_out"),
        search=("name",),
        lookup_label=("name",),
    ),
)

_SEI: tuple[EntityDef, ...] = (
    _conformed(
        "clients",
        "Clients",
        "clients",
        (
            "external_id",
            "client_id",
            "client_type",
            "first_name",
            "surname",
            "date_of_birth",
            "key",
            "as_of_date",
        ),
        search=("external_id", "client_id", "first_name", "surname"),
        lookup_label=("first_name", "surname"),
        lookup_hint="client_id",
    ),
    _conformed(
        "accounts",
        "Accounts",
        "accounts",
        (
            "external_id",
            "account_number",
            "account_name",
            "account_type",
            "status",
            "product_key",
            "key",
            "as_of_date",
        ),
        search=("external_id", "account_number", "account_name"),
        lookup_label=("account_name",),
        lookup_hint="account_number",
        note="`product_key` is ACCOUNT_TYPE, not PORTFOLIO_TYPE — the latter is an "
        "investment-purpose split. Pre-Close counts as ACTIVE: the account is winding down but "
        "still holds assets, so calling it closed hides live accounts from every status filter.",
    ),
    _conformed(
        "transactions",
        "Transactions",
        "transactions",
        ("external_id", "account_number", "transaction_type", "source_type", "as_of_date"),
        search=("external_id", "account_number"),
        note="Empty on this drop. SEI publishes 124 TRANSACTION_TYPE codes; the directionless "
        "ones resolve to NULL rather than being guessed.",
    ),
    _conformed(
        "valuations",
        "Valuations",
        "valuations",
        (
            "external_id",
            "account_external_id",
            "as_of_date",
            "market_value",
            "cash_value",
            "book_cost",
            "unrealised_gain",
            "accrued_interest",
        ),
        search=("account_external_id",),
    ),
    _conformed(
        "instruments",
        "Instruments",
        "instruments",
        ("external_id", "instrument_id", "name", "asset_type", "isin", "sedol"),
        search=("name", "isin", "sedol", "instrument_id"),
        lookup_label=("name",),
        lookup_hint="isin",
    ),
    _conformed(
        "nodes",
        "Organisations",
        "nodes",
        (
            "external_id",
            "name",
            "type",
            "parent_key",
            "identifies_firm",
            "account_count",
            "as_of_date",
        ),
        search=("name", "external_id"),
        lookup_label=("name",),
        lookup_hint="type",
        note="`platform.nodes` supersedes firms/subfirms/DSR teams and publishes no `firm_id` — "
        "which is why the uniform tenancy triple aborted SEI's whole projection.",
    ),
)

PROVIDER_ENTITIES: dict[str, tuple[EntityDef, ...]] = {
    "morgan-lloyd": _MORGAN_LLOYD,
    "seccl": _SECCL,
    "sei": _SEI,
}

# Which vendor field is a reference to which entity -- so an action form offers a PICKER over the
# firm's own records instead of a box to paste a GUID into.
#
# Worth doing because a mistyped identifier is the worst-behaved input on these forms. Dynamics
# answers an unknown contact with `0x80040217 does not exist`, which reads as an outage; an
# identifier of the WRONG KIND is worse still and is the mistake the codebase has already made
# once -- `cam_schemerefs` looked like a list of scheme references and is the reference SERIES, so
# every per-ref call was refused and four models landed empty. Hence `lookup_value` is declared
# per entity rather than assumed to be the primary key: `schemeReference` is `raw.schemes.cam_name`
# (`MLSIPP/01417`), not `cam_schemeid`.
_MORGAN_LLOYD_FIELD_LOOKUPS: dict[str, str] = {
    "contactId": "contacts",
    # "Contributor Contact ID or Account ID" -- the vendor accepts either, and a contact is the
    # case a person can actually pick from here.
    "cam_contributor": "contacts",
    "schemeReference": "schemes",
    "schemeRef": "schemes",
    "cam_FinancialAdviserContact": "advisers",
    "benefitApplicationId": "benefits",
    "cam_contributionid": "contributions",
}
FIELD_LOOKUPS: dict[str, dict[str, str]] = {"morgan-lloyd": _MORGAN_LLOYD_FIELD_LOOKUPS}


def lookup_fields(provider: str) -> dict[str, str]:
    """Vendor field name -> entity key, for the fields that are references to real records."""
    return FIELD_LOOKUPS.get(provider, {})


def lookup_entity(provider: str, key: str) -> EntityDef | None:
    """The entity a picker reads, only if it actually declares how to be picked from."""
    found = entity_for(provider, key)
    if found is None or not found.lookup_value or found.source != "lake":
        return None
    return found


# A per-tenant relation names the firm in its SCHEMA -- `tenant_{uid}.accounts`. The uid comes
# from `auth.tenants`, never from a request body, but it still reaches an identifier position, so
# it is checked rather than trusted: everything else in this module is a registry literal and this
# is the one value that is not.
_UID = re.compile(r"^[A-Za-z0-9_]+$")


def resolve(entity: EntityDef, uid: str) -> EntityDef:
    """The entity with `{uid}` filled in, for providers whose slice is a schema per firm.

    Seccl and SEI publish `tenant_{uid}.{entity}` -- the firm's slice, already computed by the
    provider's own chain. Reading THAT rather than filtering the whole-firm relation means the
    console and the pipeline cannot disagree about what a firm's slice is, which is the sort of
    second opinion that shows up as a support ticket rather than an error.
    """
    if "{uid}" not in entity.relation:
        return entity
    if not _UID.match(uid):
        raise PlatformUnavailable(f"'{uid}' is not a usable tenant identifier")
    key = tenant_model_key(uid)
    return replace(
        entity,
        relation=entity.relation.format(uid=key),
        joins=tuple(replace(j, relation=j.relation.format(uid=key)) for j in entity.joins),
    )


def entities_for(provider: str) -> tuple[EntityDef, ...]:
    return PROVIDER_ENTITIES.get(provider, ())


def entity_for(provider: str, key: str) -> EntityDef | None:
    return next((e for e in entities_for(provider) if e.key == key), None)


@dataclass(slots=True)
class _Probe:
    """What the warehouse actually has for one entity."""

    columns: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    error: str = ""
    joins: list[Join] = field(default_factory=list)
    """The joins whose other relation is actually there. Empty is normal."""
    has_slice_column: bool = True
    """Whether the relation actually carries the entity's `slice_column`.

    Probed because the DISPLAY columns were the only thing checked, while the narrowing column
    goes into the WHERE unconditionally. A relation without it -- Morgan Lloyd's `valuations`
    reads `sink.daily_valuations`, whose columns are scheme_reference / valuation_date / value /
    status / detail, with no `ml_tenant_id` anywhere -- made every count return None (swallowed)
    and every ROWS query a DuckDB binder error escaping `asyncio.to_thread` as a 500.
    """


def _probe(conn: Any, entity: EntityDef) -> _Probe:
    """Which of the declared columns are actually available, base and joined alike.

    A zero-row `LIMIT 0` read: it costs nothing and it is the difference between "this firm has
    no contacts" and "the contacts model has never run", which look identical in a table.

    A join whose other relation is missing (or has lost its key) is DROPPED, not fatal: one
    unbuilt model should cost a column, not the page it appears on.
    """
    try:
        described = conn.execute(f"SELECT * FROM {promoted_relation(entity.relation)} LIMIT 0")  # noqa: S608
        available = {d[0] for d in described.description or []}
    except Exception as exc:  # noqa: BLE001 - an unbuilt model is a state, not a crash
        return _Probe(error=str(exc).splitlines()[0][:200])

    joins: list[Join] = []
    for join in entity.joins:
        if join.column not in available:
            continue
        try:
            other = conn.execute(f"SELECT * FROM {promoted_relation(join.relation)} LIMIT 0")  # noqa: S608
            has = {d[0] for d in other.description or []}
        except Exception as exc:  # noqa: BLE001 - same reasoning as above
            logger.debug("join %s unavailable: %s", join.relation, exc)
            continue
        if join.key in has and any(c in has for c in join.label):
            joins.append(join)
    aliases = {j.alias for j in joins}
    resolvable = available | aliases
    return _Probe(
        columns=[c for c in entity.columns if c in resolvable],
        missing=[c for c in entity.columns if c not in resolvable],
        joins=joins,
        has_slice_column=entity.slice_column is None or entity.slice_column in available,
    )


def _join_sql(entity: EntityDef, joins: list[Join]) -> tuple[str, dict[str, str]]:
    """The LEFT JOIN clauses, and the SELECT expression for each alias.

    Aliased `j0`, `j1`… so a joined relation that happens to share a column name with the base
    one cannot make the query ambiguous.
    """
    clauses: list[str] = []
    expressions: dict[str, str] = {}
    base = promoted_relation(entity.relation)
    del base  # the caller owns the FROM; this only builds what hangs off it
    for index, join in enumerate(joins):
        handle = f"j{index}"
        clauses.append(
            f" LEFT JOIN {promoted_relation(join.relation)} {handle}"
            f" ON {handle}.{_quote(join.key)} = t.{_quote(join.column)}"
        )
        parts = ", ".join(f"{handle}.{_quote(c)}" for c in join.label)
        # CONCAT_WS skips NULLs, so a client with no surname reads as their first name rather
        # than as a trailing space.
        expressions[join.alias] = f"concat_ws(' ', {parts})"
    return "".join(clauses), expressions


def _select_expression(column: str, expressions: dict[str, str]) -> str:
    """How one output column is produced: a joined expression, or the base table's own column."""
    if column in expressions:
        return f"{expressions[column]} AS {_quote(column)}"
    return f"t.{_quote(column)}"


def _quote(identifier: str) -> str:
    """Double-quote an identifier. Registry values only -- never a request value.

    Belt and braces rather than trust: every name reaching this function is a literal in this
    module, and quoting means a column that happens to be a SQL keyword still works.
    """
    return '"' + identifier.replace('"', '""') + '"'


def _describe_sync(provider: str, slice_ids: list[str], uid: str = "") -> list[dict[str, Any]]:
    """Every entity, with its availability and row count for this firm."""
    defined = entities_for(provider)
    if not defined:
        return []
    with _owned_core(provider) as conn:
        out: list[dict[str, Any]] = []
        for entity in defined:
            # An outbound entity has no warehouse relation to probe -- `audit.api_log` is in the
            # portals database. Probing it here would report every one of them "not built".
            if entity.source != "lake":
                continue
            entity = resolve(entity, uid)
            probe = _probe(conn, entity)
            record: dict[str, Any] = {
                "key": entity.key,
                "label": entity.label,
                "relation": entity.relation,
                "note": entity.note,
                "columns": probe.columns,
                "missing_columns": probe.missing,
                "source": entity.source,
            }
            if entity.unavailable:
                record["availability"] = "unavailable"
                record["reason"] = entity.unavailable
            elif not is_narrowable(entity, slice_ids):
                # Named rather than served: this relation holds every firm's records and this firm
                # has no key to select its own. "Unavailable, and here is the fix" is the honest
                # answer; an unnarrowed table was the wrong one.
                record["availability"] = "unavailable"
                record["reason"] = (
                    f"{entity.relation} holds every firm's records and this firm has no "
                    f"{entity.slice_column} configured, so it cannot be narrowed to them. "
                    "Set the slice key on the firm's Platforms page."
                )
            elif not probe.has_slice_column:
                record["availability"] = "unavailable"
                record["reason"] = (
                    f"{entity.relation} has no {entity.slice_column} column, so it cannot be "
                    "narrowed to one firm. The model needs to publish it before this tab can "
                    "show anything."
                )
            elif probe.error:
                # The distinction the whole module exists for: not built is not empty.
                record["availability"] = "not_built"
                record["reason"] = (
                    f"{entity.relation} is not in the warehouse yet — the model has not run. "
                    f"({probe.error})"
                )
            else:
                record["availability"] = "ok"
                record["rows"] = _count(conn, entity, slice_ids)
            out.append(record)
        return out


class Unnarrowable(Exception):
    """This entity's relation is shared between firms and this firm has no key to narrow it by."""


def is_narrowable(entity: EntityDef, slice_ids: list[str]) -> bool:
    """Whether this entity can be restricted to one firm.

    Two safe shapes and one unsafe one:

    * `slice_column is None` -- the SCHEMA is the narrowing. Seccl's and SEI's entities read
      `tenant_{uid}.*`, a relation the provider's own chain computed for this firm alone, so
      there is nothing left to filter and nothing shared to leak.
    * a slice column AND keys -- the ordinary Morgan Lloyd case, narrowed below.
    * a slice column and NO keys -- `raw.contacts` is the whole Dynamics org. There is no
      correct query here, so there is no query.
    """
    return entity.slice_column is None or bool(slice_ids)


def _slice_clause(entity: EntityDef, slice_ids: list[str]) -> tuple[str, list[Any]]:
    """The per-firm narrowing.

    A LIST, not one value: `platform.tenant_platforms.config` keys Morgan Lloyd on `tenant_ids`,
    and a firm running more than one Dynamics tenant holds several. `IN` over one id is the same
    query plan as `=`, so there is no reason to special-case the common shape.

    NO KEYS ON A SHARED RELATION IS A REFUSAL, and this is a reversal. It used to return no WHERE
    at all, reasoning that an empty console reads as "this custodian holds nothing for us" when it
    means "nobody finished the enablement", and that the page states the firm's keys beside the
    tables so an unnarrowed view is visibly unnarrowed. But `raw.contacts` is every contact in the
    Dynamics org: the mitigation was UI copy on one page, while the property being relied on was an
    API one -- `GET /admin/platforms/morgan_lloyd/firms/{uid}/entities/contacts` handed another
    firm's clients to any `platforms:read` holder, for any firm whose config was left blank.
    Telling the operator WHY the tab is empty is `availability: "unavailable"`'s job, which costs
    nothing and leaks nothing.
    """
    if not is_narrowable(entity, slice_ids):
        raise Unnarrowable(entity.relation)
    if entity.slice_column is None:
        return "", []
    placeholders = ", ".join("?" for _ in slice_ids)
    # Qualified `t.`: a joined relation can carry a column of the same name, and an unqualified
    # reference would be ambiguous rather than merely wrong.
    return f" WHERE t.{_quote(entity.slice_column)} IN ({placeholders})", list(slice_ids)


def _count(conn: Any, entity: EntityDef, slice_ids: list[str]) -> int | None:
    where, params = _slice_clause(entity, slice_ids)
    try:
        relation = f"{promoted_relation(entity.relation)} t"
        row = conn.execute(f"SELECT count(*) FROM {relation}{where}", params).fetchone()  # noqa: S608
        return int(row[0]) if row else 0
    except Exception as exc:  # noqa: BLE001
        logger.debug("count unavailable for %s: %s", entity.relation, exc)
        return None


def _rows_sync(
    provider: str,
    entity: EntityDef,
    *,
    slice_ids: list[str],
    uid: str = "",
    q: str,
    sort: str | None,
    descending: bool,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    entity = resolve(entity, uid)
    with _owned_core(provider) as conn:
        probe = _probe(conn, entity)
        if probe.error:
            raise PlatformUnavailable(
                f"{entity.relation} is not in the warehouse yet — the model has not run."
            )
        if not probe.columns:
            raise PlatformUnavailable(
                f"{entity.relation} has none of the columns this console shows; the vendor's "
                "field names may have changed."
            )
        # Refused, not attempted: without this column there is no query that returns THIS firm's
        # rows, and the alternative was a binder error surfacing as a 500.
        if not probe.has_slice_column:
            raise PlatformUnavailable(
                f"{entity.relation} has no {entity.slice_column} column, so it cannot be narrowed "
                "to one firm."
            )

        joined, expressions = _join_sql(entity, probe.joins)
        relation = f"{promoted_relation(entity.relation)} t{joined}"
        where, params = _slice_clause(entity, slice_ids)

        # Free text: ILIKE across the declared search columns, narrowed to those actually
        # available. One bound parameter reused per column. A joined alias searches its
        # expression, so "search schemes for Halloway" matches the client's name.
        searchable = [c for c in entity.search if c in probe.columns]
        if q and searchable:
            predicate = " OR ".join(
                f"CAST({expressions.get(c, f't.{_quote(c)}')} AS VARCHAR) ILIKE ?"
                for c in searchable
            )
            where = f"{where} AND ({predicate})" if where else f" WHERE ({predicate})"
            params += [f"%{q}%"] * len(searchable)

        total = int(conn.execute(f"SELECT count(*) FROM {relation}{where}", params).fetchone()[0])  # noqa: S608

        # The sort column is validated against the probe, so an unknown one is ignored rather
        # than interpolated. `NULLS LAST` in both directions matches DataTable's own rule --
        # blanks last -- so the server and the client agree about a half-populated column.
        column = sort if sort in probe.columns else entity.order_by
        order = ""
        if column in probe.columns:
            target = expressions.get(column, f"t.{_quote(column)}")
            order = f" ORDER BY {target} {'DESC' if descending else 'ASC'} NULLS LAST"

        selected = ", ".join(_select_expression(c, expressions) for c in probe.columns)
        cursor = conn.execute(
            f"SELECT {selected} FROM {relation}{where}{order} LIMIT ? OFFSET ?",  # noqa: S608
            [*params, min(max(limit, 1), _MAX_LIMIT), max(offset, 0)],
        )
        names = [d[0] for d in cursor.description or []]
        items = [
            {name: _cell(value) for name, value in zip(names, row, strict=True)}
            for row in cursor.fetchall()
        ]
        return {
            "columns": probe.columns,
            "missing_columns": probe.missing,
            "items": items,
            "total": total,
        }


_LOOKUP_LIMIT = 50


def _options_sync(
    provider: str, entity: EntityDef, *, slice_ids: list[str], q: str, uid: str = ""
) -> list[dict[str, Any]]:
    """Pickable rows for one lookup field: `{value, label, hint}`, newest first.

    Capped at 50 with no paging, deliberately: a picker is a search box, not a table. The firm's
    own tab is where you go to browse 936 benefit applications; here you type enough to find the
    one you mean. The cap being visible in the UI is what stops "it isn't in the list" being read
    as "it does not exist".
    """
    entity = resolve(entity, uid)
    with _owned_core(provider) as conn:
        probe = _probe(conn, entity)
        if probe.error or entity.lookup_value not in probe.columns:
            # An unbuilt relation or a renamed key column: the caller falls back to a free-text
            # box rather than showing an empty picker, which would read as "this firm has none".
            return []
        if not is_narrowable(entity, slice_ids) or not probe.has_slice_column:
            # The same refusal as the table, degraded the way this path degrades. A picker is the
            # easiest place to leak cross-firm identifiers -- it exists to be typed into and it
            # shows names -- so an unnarrowable lookup offers nothing rather than everything.
            return []
        labels = [c for c in entity.lookup_label if c in probe.columns]
        hint = entity.lookup_hint if entity.lookup_hint in probe.columns else None

        selected = [f"t.{_quote(entity.lookup_value)}", *(f"t.{_quote(c)}" for c in labels)]
        if hint:
            selected.append(f"t.{_quote(hint)}")
        where, params = _slice_clause(entity, slice_ids)
        # Searchable over the label columns AND the identifier itself -- an operator who already
        # has the GUID from somewhere else should be able to confirm it here.
        searchable = [*labels, entity.lookup_value]
        if q:
            predicate = " OR ".join(f"CAST(t.{_quote(c)} AS VARCHAR) ILIKE ?" for c in searchable)
            where = f"{where} AND ({predicate})" if where else f" WHERE ({predicate})"
            params += [f"%{q}%"] * len(searchable)
        # The identifier must exist: a row with none is not pickable, and offering it produces a
        # call the vendor refuses.
        blank = f"t.{_quote(entity.lookup_value)} IS NOT NULL"
        where = f"{where} AND {blank}" if where else f" WHERE {blank}"

        order = f" ORDER BY t.{_quote(entity.order_by)} DESC NULLS LAST" if entity.order_by else ""
        cursor = conn.execute(
            f"SELECT {', '.join(selected)} FROM {promoted_relation(entity.relation)} t"  # noqa: S608
            f"{where}{order} LIMIT ?",
            [*params, _LOOKUP_LIMIT],
        )
        out: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            values = [_cell(v) for v in row]
            parts = [str(v) for v in values[1 : 1 + len(labels)] if v not in (None, "")]
            out.append(
                {
                    "value": str(values[0]),
                    # Falls back to the identifier rather than rendering a blank row: an
                    # unnamed record is still pickable, and an empty line is not.
                    "label": " ".join(parts) or str(values[0]),
                    "hint": str(values[-1]) if hint and values[-1] not in (None, "") else "",
                }
            )
        return out


async def options(
    provider: str, entity: EntityDef, *, slice_ids: list[str], q: str = "", uid: str = ""
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _options_sync, provider, entity, slice_ids=slice_ids, q=q, uid=uid
    )


def _cell(value: Any) -> Any:
    """JSON-safe. A date, a Decimal or a UUID becomes its string; everything else passes."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


# --- the outbound reader ---------------------------------------------------------------------
#
# `audit.api_log` is in the portals database, not the warehouse, so these read through the
# request's own session rather than the lake attach. The projection is a module constant and the
# operations are a registry literal; the tenant and the paging are bound.
_OUTBOUND_SELECT = """
    SELECT l.created_at, l.operation, l.entity_id, l.ok, l.status_code, l.error, l.duration_ms,
           coalesce(u.email, '') AS actor
      FROM audit.api_log l
      LEFT JOIN auth.users u ON u.id = l.user_id
     WHERE l.provider = :provider
       AND l.operation = ANY(:operations)
       AND l.tenant_id = :tenant_id
"""


def _outbound_operations(entity: EntityDef) -> list[str]:
    return list(entity.operations)


async def _describe_outbound(
    session: Any, provider: str, entity: EntityDef, tenant_id: int | None
) -> dict[str, Any]:
    """Availability for an outbound tab.

    It is always `ok` -- the log table always exists, so "no rows" here genuinely means "we have
    sent none", which is a complete answer rather than an absence of one. `unattributed` is the
    one caveat worth carrying: a call recorded without a firm belongs to no tab, and scoping it
    strictly to this firm (the only honest choice -- attributing it here would assert something
    we did not record) would otherwise make it invisible everywhere.
    """
    from sqlalchemy import text

    record: dict[str, Any] = {
        "key": entity.key,
        "label": entity.label,
        "relation": entity.relation,
        "note": entity.note,
        "columns": list(entity.columns),
        "missing_columns": [],
        "source": entity.source,
        "availability": "ok",
    }
    operations = _outbound_operations(entity)
    row = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE tenant_id = :tenant_id) AS mine, "
                "count(*) FILTER (WHERE tenant_id IS NULL) AS unattributed "
                "FROM audit.api_log WHERE provider = :provider AND operation = ANY(:operations)"
            ),
            {"provider": provider, "operations": operations, "tenant_id": tenant_id},
        )
    ).one()
    record["rows"] = int(row.mine or 0)
    if row.unattributed:
        record["unattributed"] = int(row.unattributed)
    return record


async def _rows_outbound(
    session: Any,
    provider: str,
    entity: EntityDef,
    *,
    tenant_id: int | None,
    q: str,
    sort: str | None,
    descending: bool,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    from sqlalchemy import text

    params: dict[str, Any] = {
        "provider": provider,
        "operations": _outbound_operations(entity),
        "tenant_id": tenant_id,
    }
    clause = ""
    if q:
        # One bound parameter across the searchable columns, cast so a NULL `error` does not
        # swallow the row.
        predicate = " OR ".join(
            f"coalesce(CAST(l.{c} AS text), '') ILIKE :q" for c in _OUTBOUND_SEARCH
        )
        clause = f" AND ({predicate})"
        params["q"] = f"%{q}%"

    # S608 on both statements below: the only interpolated part is `clause`, built above from
    # `_OUTBOUND_SEARCH` (a module constant) alone -- the search TEXT is the bound `:q`, and the
    # ORDER BY column is validated against the entity's declared set. No caller value reaches the
    # SQL string.
    counted = (
        "SELECT count(*) FROM audit.api_log l WHERE l.provider = :provider "  # noqa: S608
        f"AND l.operation = ANY(:operations) AND l.tenant_id = :tenant_id{clause}"
    )
    total = (await session.execute(text(counted), params)).scalar_one()

    # Validated against the declared set, exactly as the lake path validates against the probe.
    column = sort if sort in entity.columns else entity.order_by
    order = "created_at" if column not in entity.columns else column
    prefix = "" if order == "actor" else "l."
    direction = "DESC" if descending else "ASC"
    listed = (  # noqa: S608
        f"{_OUTBOUND_SELECT}{clause} "
        f"ORDER BY {prefix}{order} {direction} NULLS LAST, l.id DESC "
        "LIMIT :limit OFFSET :offset"
    )
    result = await session.execute(
        text(listed),
        {**params, "limit": min(max(limit, 1), _MAX_LIMIT), "offset": max(offset, 0)},
    )
    items = [
        {name: _cell(value) for name, value in dict(mapping).items()}
        for mapping in result.mappings()
    ]
    return {
        "columns": list(entity.columns),
        "missing_columns": [],
        "items": items,
        "total": int(total or 0),
    }


async def describe(
    provider: str,
    slice_ids: list[str],
    *,
    session: Any = None,
    tenant_id: int | None = None,
    uid: str = "",
) -> list[dict[str, Any]]:
    """Every entity, lake-backed and outbound alike, in registry order.

    The two sources are read separately -- one lake connection for all of the warehouse
    relations, one query per outbound tab -- and merged back into the declared order, because the
    order is the console's navigation and it must not depend on where a tab's rows live.
    """
    defined = entities_for(provider)
    outbound = {e.key for e in defined if e.source == "outbound"}
    lake = await asyncio.to_thread(_describe_sync, provider, slice_ids, uid)
    by_key = {record["key"]: record for record in lake}
    if session is not None:
        for entity in defined:
            if entity.key in outbound:
                by_key[entity.key] = await _describe_outbound(session, provider, entity, tenant_id)
    return [by_key[e.key] for e in defined if e.key in by_key]


async def rows(
    provider: str,
    entity: EntityDef,
    *,
    slice_ids: list[str],
    q: str = "",
    sort: str | None = None,
    descending: bool = True,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    session: Any = None,
    tenant_id: int | None = None,
    uid: str = "",
) -> dict[str, Any]:
    if entity.source == "outbound":
        if session is None:
            raise PlatformUnavailable(
                f"{entity.label} is our own outbound record, which needs a database session."
            )
        return await _rows_outbound(
            session,
            provider,
            entity,
            tenant_id=tenant_id,
            q=q,
            sort=sort,
            descending=descending,
            limit=limit,
            offset=offset,
        )
    return await asyncio.to_thread(
        _rows_sync,
        provider,
        entity,
        slice_ids=slice_ids,
        uid=uid,
        q=q,
        sort=sort,
        descending=descending,
        limit=limit,
        offset=offset,
    )
