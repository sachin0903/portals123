"""The contract between a provider's conformed layer and the book.

One declaration per entity, carrying three things and nothing else:

* the STAGING projection -- DuckDB SQL that renames the conformed columns to the book's own
  column names. It is deliberately thin: a value that needs deriving belongs in the provider's
  conformed model (docs/platform-model.md §3), where it is one statement for every tenant and is
  tested in DuckDB, not here, where it would be re-derived per tenant per run.
* the IDENTITY -- a natural key (platform.merge_entity) or a link (platform.merge_linked_entity).
* the PARENTS -- the foreign keys resolved against the book itself, and which of them a row cannot
  be written without.

WHY THE ORDER IS FIXED: each entity resolves its parents against the ones before it. Orgs before
contacts before accounts before valuations and transactions. A row whose REQUIRED parent is absent
is recorded in platform.merge_rejects and not written -- never written with a NULL that later reads
as data.

WHAT IS NOT HERE, and why: `positions`. Holdings are read from the lake by every surface that shows
them, so there is no OLTP table to merge into -- it is projected into the tenant's lake and stops
there. Everything else Seccl conforms is merged.

One entity is not a projection at all: a model VERSION is a decision about a SET of rows compared
against the set the book holds, keyed by `max(version) + 1`. `Entity.procedure` names the
migration-owned function that merges it (0068), with the same signature as the generic merges so the
ledger stays uniform.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import sql_str

_READS = re.compile(r"\bs\.([a-z_][a-z0-9_]*)")


def required_columns(entity: Entity) -> frozenset[str]:
    """Every conformed column this entity's projection reads, from the projection itself.

    The contract a provider has to meet before it can be wired, in a form a test can check: a
    column the sink reads and the provider does not publish is a DuckDB Binder Error that aborts
    the whole tenant, and the ledger records that as "unavailable" -- indistinguishable from "this
    firm has no data with this provider".

    Read off the SQL rather than declared beside it, because a declared list is a second place to
    forget: the projection is the truth about what it reads.
    """
    return frozenset(_READS.findall(entity.select) + _READS.findall(entity.from_sql))


#: The schema a provider's conformed layer is published under, core-side. `platform` is the
#: Graphene-canonical shape (as opposed to `raw`/`staging`, which are provider-shaped); which
#: provider a relation came from is already the catalogue, so repeating it here carries nothing.
PLATFORM_SCHEMA = "platform"


@dataclass(frozen=True)
class Parent:
    """A foreign key resolved against the book, not against the slice."""

    #: The book column this fills in, e.g. `contact_id`.
    column: str
    #: The landing column holding the provider's key for it, e.g. `client_external_id`.
    #: Empty for a parent that needs no key from the provider (`platform`, which is the provider).
    source: str
    #: How to resolve it, against the book:
    #:   `link:<entity_type>`  a platform.links row for this tenant and provider
    #:   `org`                 platform.nodes' natural key (tenant, provider, key)
    #:   `unique:<table>:<column>`  a tenant-scoped row by its own unique column, e.g.
    #:                         `unique:platform.products:product_type` (the firm's shelf key) or
    #:                         `unique:platform.fee_packages:external_id`
    #:   `platform`            platform.platforms by provider -- the registry the API seeds on
    #:                         startup from providers.PROVIDERS, not from a migration
    via: str
    #: A row whose parent does not resolve is a reject, not a row with a NULL.
    required: bool = False


@dataclass(frozen=True)
class Requirement:
    """A condition a staged row must meet, and what the ledger says when it does not.

    Not a WHERE clause in the staging SQL, deliberately: a row silently filtered out is
    indistinguishable from a row the provider never sent. `check` is SQL over the staged row `s`;
    `reason` is a SQL expression, so the ledger can name the value that failed.
    """

    check: str
    reason: str


@dataclass(frozen=True)
class Entity:
    """One conformed relation and how it becomes rows in the book."""

    #: The conformed relation's name in the provider's `platform` schema.
    source: str
    #: The book table it merges into.
    target: str
    #: The staging projection: a DuckDB SELECT list, `{tenant_id}` / `{provider}` interpolated.
    select: str
    #: The FROM clause. `{rel[<entity>]}` names another of this tenant's filtered relations, so an
    #: entity that needs a sibling (accounts needs its valuation) says so rather than being
    #: patched up by a second pass over the book.
    from_sql: str = "FROM {rel[%(source)s]} AS s"
    #: platform.links.entity_type when identity is a link; None when the target has a natural key.
    link_type: str | None = None
    #: The target's natural key, for platform.merge_entity.
    key: tuple[str, ...] = ()
    #: A partial unique index's predicate, when the key is enforced by one. Without it Postgres
    #: cannot infer the index and answers "no unique or exclusion constraint matching".
    conflict_where: str = ""
    #: A migration-owned function that merges this entity INSTEAD of the generic policy, for the
    #: one shape the policy cannot express: a model version is a decision about a SET of rows
    #: compared against the set the book holds, keyed by `max(version) + 1`. Same signature as the
    #: generic merges (source, tenant, provider) -> (inserted, updated), so the ledger is uniform.
    procedure: str = ""
    #: Columns the provider owns after the first sighting. `('*',)` means the whole row.
    updatable: tuple[str, ...] = ()
    #: Filled on a later run only while the book still holds NULL. A value the firm has
    #: typed since is left alone — the custodian seeds a blank it did not have at first sighting.
    fill_if_null: tuple[str, ...] = ()
    #: Replaced on a later run only while the book still holds the provider's own id. The
    #: staging projection writes that id when the provider named nothing, so the first
    #: sighting stores the id as the name. A later read that has a real name replaces the
    #: placeholder. A value that is not the id is the firm's, and is left alone.
    replace_placeholder: tuple[str, ...] = ()
    #: Replaced on a later run only while the book still holds a bare number. SEI's first
    #: load stored the TITLE code (`1`) before the reference was joined, and a courtesy
    #: title the firm has since typed is not a number, so it is left alone.
    replace_numeric: tuple[str, ...] = ()
    #: Columns to keep out of the ENTITY even though the source and target share them. One member:
    #: `crm.contacts.data`. The projection must stage `data` because that is where the LINK's
    #: payload comes from, and the payload belongs there and nowhere else — one entity can hold
    #: links to two providers, and one column cannot hold two payloads.
    exclude: tuple[str, ...] = ()
    parents: tuple[Parent, ...] = ()
    #: Other filtered relations this entity's staging SQL reads.
    reads: tuple[str, ...] = ()
    #: Conditions beyond parent resolution; a row failing one is a counted reject.
    requires: tuple[Requirement, ...] = ()
    #: Conditions whose failure is WORTH COUNTING but does not stop the row: Seccl lists SSAS
    #: among a model's wrappers and our enum has no member for it, which does not invalidate the
    #: model -- but its arrival is how we find out the enum needs extending. Counted in the ledger,
    #: never in platform.merge_rejects, because the row WAS written.
    observes: tuple[Requirement, ...] = ()
    #: Columns identifying an existing book row this provider is seeing for the first time --
    #: platform.instruments is global and deduped by ISIN, so two tenants must reach the same row.
    #: The link is pre-minted against the match, and the merge then declines to insert a duplicate.
    adopt_on: tuple[str, ...] = ()
    #: The staged column naming a row in the provider's own terms, for the reject ledger.
    row_key: str = "external_id"
    #: What this entity is CALLED, when that differs from the relation it reads. Two entities can
    #: share a source: the firm's shelf and each platform's own reference for it both come from
    #: `accounts`, and they must not collide on a landing table or a ledger line.
    name: str = ""

    @property
    def slug(self) -> str:
        return self.name or self.source

    def relations(self) -> tuple[str, ...]:
        return (self.source, *self.reads)

    def staging_sql(self, *, tenant_id: int, provider: str, relations: dict[str, str]) -> str:
        """The staging model's SQL: the book's own column names, from the tenant's filtered slice.

        Identifiers come from this module's declarations and the tenant registry; the only
        interpolated VALUES are the tenant id (an int) and the provider name (from our own
        registry), so there is no caller-supplied SQL here.
        """
        # `role` is a pure function of the provider (`PROVIDER_META['role']`), so it is derived
        # here rather than declared per entity: the product_platforms projection is the only
        # reader, and hard-coding "custodian" there would silently mis-file the scheme
        # administrator's mapping the day a second administrator is added.
        columns = self.select.format(
            tenant_id=tenant_id,
            provider=provider,
            role=sql_str(PROVIDER_ROLES[provider]) if provider in PROVIDER_ROLES else "NULL",
            # Only `_TRANSACTIONS` reads this. SEI's displayed figure is the cash that
            # moved (net), then the book value of a cashless transfer. Every other
            # provider keeps gross, then net: Seccl's headline is the executed amount,
            # and consideration is often empty.
            amount=(
                "coalesce(nullif(s.net_amount, 0), nullif(s.gross_amount, 0),"
                " nullif(s.book_value, 0), 0)"
                if provider == "sei"
                else "coalesce(nullif(s.gross_amount, 0), s.net_amount, 0)"
            ),
        ).strip()
        frm = (self.from_sql % {"source": self.source}).format(rel=relations)
        return f"SELECT\n{columns}\n{frm}"  # noqa: S608 - see docstring


# --- the wrapper vocabulary ---------------------------------------------------------------------
#
# The canonical product types, the ONE alias a provider needs, and the label each wrapper is called
# by. Rendered into SQL below rather than applied row by row in Python.
#
# A fourth copy of the labels, temporarily: app/materialise.py has one (it goes with it in Phase 7),
# `apps/admin/src/lib/catalogue/types.ts::PRODUCT_TYPE_LABELS` needs its own for the vocabulary rows
# a firm has no product record for yet, and `0059_product_names.sql` backfilled with them. The name
# is only ever set on INSERT, so this is a starting label and never an override of a firm's rename.

PRODUCT_TYPES: tuple[str, ...] = (
    "GIA",
    "ISA",
    "JISA",
    "SIPP",
    "TPONBOND",
    "TPOFFBOND",
    "TPPENSION",
)

#: Seccl calls the SIPP wrapper PENSION. Our enum has said SIPP since 0002 and already spends
#: TPPENSION on the third-party case, so adding PENSION beside it would give one concept two
#: spellings. An alias, declared -- NOT a general normaliser: an unlisted wrapper stays a counted
#: reject, because inventing a mapping is how a firm's shelf ends up wrong.
PRODUCT_ALIASES: dict[str, str] = {"PENSION": "SIPP"}

#: WHICH WRAPPERS ARE PENSIONS, for `platform.products.is_pension` — which since 0136 is what SHOWS
#: the client record's Pension and Employer sections and the account's Beneficiaries panel.
#:
#: STAGED, because discovery is what creates a product. `is_pension` is NOT NULL DEFAULT false and
#: this projection never wrote it, so every product the pipeline discovered took `false` and no
#: later run corrected it — 0135's one-off UPDATE patched the rows it found and left the writer
#: alone, which is the "the table is there and nothing populates it" shape that cost every account
#: its `product_id`. A firm whose shelf arrives from a nightly run would have had the Pension
#: section hidden for every client holding a SIPP.
#:
#: TPPENSION is HERE and is deliberately absent from the integration gates: a third-party pension
#: is administered elsewhere and is not a Morgan Lloyd scheme, so what is SHOWN and what is SENT
#: are different questions. This is the shown one.
PENSION_WRAPPERS: tuple[str, ...] = ("SIPP", "SSAS", "TPPENSION")

#: WHAT EACH PROVIDER DOES FOR A PRODUCT (`platform.product_platforms.role`, 0122). Declared here
#: beside the wrapper vocabulary for the reason that one is: the sink runs inside the provider's own
#: DAG and cannot import the API's registry, and 0122's CHECK is the shared contract. It mirrors
#: `app/providers.py::PROVIDER_META[...]["role"]`, and an unlisted provider stamps NULL rather than
#: guessing — a wrong role is worse than none, because `GET /catalog/products` PREFERS the
#: custodian's mapping when naming the platform a product is held at.
PROVIDER_ROLES: dict[str, str] = {
    "morgan-lloyd": "scheme_administrator",
    "seccl": "custodian",
    "sei": "custodian",
}

PRODUCT_LABELS: dict[str, str] = {
    "GIA": "General Investment Account",
    "ISA": "Individual Savings Account",
    "JISA": "Junior Individual Savings Account",
    "SIPP": "Self-Invested Personal Pension",
    "TPONBOND": "Third-party Onshore Bond",
    "TPOFFBOND": "Third-party Offshore Bond",
    "TPPENSION": "Third-party Pension",
}


def _case(expression: str, mapping: dict[str, str]) -> str:
    """`CASE <expression> WHEN 'a' THEN 'b' … END` — NULL for anything unlisted, which is what the
    reject and the observation downstream key on."""
    arms = " ".join(f"WHEN '{key}' THEN '{value}'" for key, value in mapping.items())
    return f"CASE {expression} {arms} END"


#: provider wrapper string -> canonical product_type. Every canonical type maps to itself, plus the
#: declared aliases.
_TYPE_MAP: dict[str, str] = {**{name: name for name in PRODUCT_TYPES}, **PRODUCT_ALIASES}

_PRODUCT_TYPE = _case("upper(CAST(s.product_key AS VARCHAR))", _TYPE_MAP)
_PRODUCT_LABEL = _case(_PRODUCT_TYPE, PRODUCT_LABELS)


# --- the staging projections ------------------------------------------------------------------
#
# Each is the conformed->book rename, verbatim from what app/materialise.py's upserts read. Where
# materialise.py applied a Python-side default or coalesce, it is written here as SQL so the value
# is decided once per run in the engine rather than once per row in the daemon.

# `data` is passed through, never re-wrapped: the conformed layer already publishes `to_json(t)`,
# so a second to_json() would land a JSON string INSIDE a JSON string and every reader of
# links.data would have to know which layer double-encoded it.

_ORGS = """
  {tenant_id} AS tenant_id,
  '{provider}' AS provider,
  CAST(coalesce(s.key, s.external_id) AS VARCHAR) AS key,
  CAST(s.lineage AS VARCHAR[]) AS lineage,
  -- The CANONICAL value, conformed by the provider. This used to map the provider's raw word here,
  -- which put a per-provider vocabulary in the sink -- and SEI, whose own hierarchy already yields
  -- 'firm'/'subfirm'/'team', published nothing called `type_raw`. A vocabulary is part of a
  -- shape, so it belongs in the layer whose job is one shape (docs/platform-model.md §3).
  CAST(s.type AS VARCHAR) AS type,
  CAST(s.name AS VARCHAR) AS name,
  CAST(s.parent_key AS VARCHAR) AS parent_key,
  CAST(s.external_id AS VARCHAR) AS external_id,
  CAST(s.data AS VARCHAR) AS data
"""

_INSTRUMENTS = """
  CAST(s.external_id AS VARCHAR) AS external_id,
  CAST(s.isin AS VARCHAR) AS isin,
  CAST(s.sedol AS VARCHAR) AS sedol,
  CAST(s.cusip AS VARCHAR) AS cusip,
  -- NOT NULL in the book, and an instrument with no name is still an instrument: fall back to the
  -- provider's id, which is what it is known by anyway.
  coalesce(CAST(s.name AS VARCHAR), CAST(s.external_id AS VARCHAR)) AS name,
  CAST(s.legal_name AS VARCHAR) AS legal_name,
  CAST(s.asset_type AS VARCHAR) AS asset_type,
  CAST(s.currency AS VARCHAR) AS currency,
  CAST(s.exchange_code AS VARCHAR) AS exchange_code,
  CAST(s.market_country AS VARCHAR) AS market_country,
  CAST(s.issuer_country AS VARCHAR) AS issuer_country,
  CAST(s.credit_rating AS VARCHAR) AS credit_rating,
  s.interest_rate,
  s.issued_on,
  s.matures_on,
  -- The fund's ongoing charge, as the custodian published it. Filled while the book
  -- still holds NULL: a later blank (Seccl publishes none; SEI's file is mostly empty)
  -- must not clear a figure already stored.
  s.ocf,
  s.source_updated_at,
  CAST(s.data AS VARCHAR) AS data
"""

_CLIENTS = """
  {tenant_id} AS tenant_id,
  CAST(s.external_id AS VARCHAR) AS external_id,
  -- The conformed vocabulary is individual | entity; the book's is individual | organisation.
  CASE WHEN s.client_type = 'individual' THEN 'individual' ELSE 'organisation' END AS type,
  -- trust | company | charity, and only on an organisation. A person publishes NULL
  -- (the book forbids a sub-type on an individual). A kind the enum does not know
  -- publishes NULL rather than a value the cast would reject.
  CASE
    WHEN s.client_type = 'individual' THEN NULL
    WHEN s.sub_type IN ('trust', 'company', 'charity') THEN s.sub_type
  END AS sub_type,
  -- The book's `name` is the display name: the provider's entity name when it has one, else the
  -- parts joined. concat_ws skips NULLs, which is what makes a missing middle name harmless. For a
  -- person with a first name and a surname the database replaces it with their parts (0239).
  nullif(coalesce(CAST(s.entity_name AS VARCHAR),
                  trim(concat_ws(' ', s.first_name, s.middle_name, s.surname))), '') AS name,
  CAST(s.title AS VARCHAR) AS title,
  CAST(s.first_name AS VARCHAR) AS first_name,
  CAST(s.middle_name AS VARCHAR) AS middle_name,
  CAST(s.surname AS VARCHAR) AS surname,
  s.date_of_birth AS dob,
  -- A LIST, because `crm.contacts.nationality` is `text[]`: a person can hold two. The cast is to
  -- VARCHAR[] rather than VARCHAR for that reason, and it is load-bearing twice over -- a provider
  -- publishing a scalar fails here as a conversion error instead of reaching the book, where
  -- Postgres would answer "malformed array literal" from inside the merge; and DuckDB's
  -- list_extract on a VARCHAR does STRING indexing, so taking "the first element" of a scalar 'GB'
  -- would have silently yielded 'G'.
  CAST(s.nationality AS VARCHAR[]) AS nationality,
  CAST(s.national_identifier AS VARCHAR) AS national_identifier,
  CAST(s.national_identifier_type AS VARCHAR) AS national_identifier_type,
  -- Only a word the book's CHECK allows. A provider code that was not translated
  -- becomes NULL, which fails the row closed instead of aborting the tenant.
  CASE WHEN s.gender IN ('male', 'female') THEN s.gender END
    AS gender,
  CASE WHEN s.marital_status IN ('single', 'married', 'divorced', 'widowed')
       THEN s.marital_status END AS marital_status,
  s.is_vulnerable,
  CASE WHEN regexp_matches(upper(trim(CAST(s.country_of_birth AS VARCHAR))), '^[A-Z]{{2}}$')
       THEN upper(trim(CAST(s.country_of_birth AS VARCHAR))) END AS country_of_birth,
  CASE WHEN s.fatca_status IN
            ('w9', 'w8', 'w8_cure', 'other_individual', 'other_entity', 'not_certified')
       THEN s.fatca_status END AS fatca_status,
  -- W-9 with us_person false, or W-8 with us_person true, fails the coherence check
  -- and takes the whole client merge with it. Those two pairs are dropped to NULL
  -- on the person flag; the certification itself is kept.
  CASE
    WHEN s.fatca_status = 'w9' AND s.us_person IS FALSE THEN NULL
    WHEN s.fatca_status = 'w8' AND s.us_person IS TRUE THEN NULL
    ELSE s.us_person
  END AS us_person,
  CAST(s.passport_number AS VARCHAR) AS passport_number,
  CASE WHEN regexp_matches(upper(trim(CAST(s.passport_country AS VARCHAR))), '^[A-Z]{{2}}$')
       THEN upper(trim(CAST(s.passport_country AS VARCHAR))) END AS passport_country,
  CAST(s.email AS VARCHAR) AS email,
  -- Two scalars. `mobile` is the number every custodian can store; `phone` is the other one
  -- (Seccl's landline object, Morgan Lloyd's `telephone1`, a second SEI contact-detail row).
  -- Not in `updatable`: a number the firm has since corrected is left alone. `fill_if_null`
  -- still writes one when the book holds nothing, so a later drop can fill a blank.
  CAST(s.mobile AS VARCHAR) AS mobile,
  CAST(s.phone AS VARCHAR) AS phone,
  to_json(s.address) AS address,
  -- The custodian's own status for the PERSON. Seccl publishes it (suspended is the one we
  -- surface); Morgan Lloyd maps Dataverse inactive; SEI has no person-level status and
  -- publishes NULL. lower() so a title-cased payload and the conformed column compare equal
  -- to the listing's derivation. The firm's Close is `deleted_at`, a different column.
  lower(CAST(s.status AS VARCHAR)) AS provider_status,
  -- The client's base currency. Seccl and Morgan Lloyd publish GBP; SEI publishes
  -- CLIENT_BASE_CURRENCY. A blank becomes GBP, which is also the column default.
  -- A code the seed does not list is left as written here; the book merge stores GBP
  -- for it (crm.coerce_unknown_contact_currency) so one unknown code cannot abort the client.
  coalesce(nullif(upper(trim(CAST(s.base_currency AS VARCHAR))), ''), 'GBP') AS currency,
  CAST(s.key AS VARCHAR) AS key,
  s.source_updated_at,
  CAST(s.data AS VARCHAR) AS data
"""

_ACCOUNTS = f"""
  {{tenant_id}} AS tenant_id,
  CAST(s.external_id AS VARCHAR) AS external_id,
  -- The lead owner, or the first: an account whose client has not materialised is still worth
  -- creating -- it exists, and the next run links it.
  coalesce(CAST(s.lead_client_external_id AS VARCHAR),
           CAST(list_extract(s.owner_external_ids, 1) AS VARCHAR)) AS client_external_id,
  CAST(s.key AS VARCHAR) AS key,
  -- The conformed layer owns the mapping onto platform.account_status and publishes NULL for a
  -- word it does not know; 'active' is then the book's own default, as it was before.
  coalesce(CAST(s.status AS VARCHAR), 'active') AS status,
  CAST(s.account_name AS VARCHAR) AS name,
  coalesce(nullif(upper(trim(CAST(s.base_currency AS VARCHAR))), ''), 'GBP') AS currency,
  s.is_discretionary,
  -- The provider's own keys for the wrapper and the model, resolved against the book by the merge.
  CAST(s.product_key AS VARCHAR) AS product_key,
  -- Seccl's client product: one id per tax wrapper, carried on every account of that wrapper.
  -- Not a column of platform.accounts. The merge writes it onto the client as a link.
  CAST(s.product_external_id AS VARCHAR) AS product_external_id,
  -- THE CANONICAL TYPE, and the account's product parent joins on THIS, not on `product_key`.
  --
  -- `PRODUCT_ALIASES` was applied when DISCOVERING a product (_PRODUCTS, _PRODUCT_PLATFORMS,
  -- _MODEL_PRODUCTS, _APPLIES_TO) and nowhere on the account, so the parent compared Seccl's raw
  -- 'PENSION' against a `platform.products.product_type` holding the aliased 'SIPP' and never
  -- matched: 98 of 379 accounts on the dev book -- every Seccl pension -- carried a NULL
  -- `product_id`. That is not a cosmetic gap. Five surfaces gate on the product_type resolved
  -- through it, so all five were dark for every real SIPP: drawdown answered "Drawdown applies to
  -- pension accounts only", `provider_positions` never rendered the 0117 crystallisation and
  -- scheme-cash block, the ML valuation push refused, and the contributions pension filter and the
  -- MPAA allowance gate both missed. It is the SAME failure the comment on this entity's parents
  -- already describes as fixed -- it was fixed one join short.
  --
  -- Published BESIDE `product_key` rather than replacing it: the provider's own word is what makes
  -- a reject legible ("unmodelled wrapper: TPONBOND"), and an unlisted wrapper still resolves to
  -- NULL here, which is a NULL parent rather than a failed cast.
  {_PRODUCT_TYPE} AS product_type,
  CAST(s.model_key AS VARCHAR) AS model_key,
  -- The balance IS the provider's to state (the declared exception in 0064): redundant with
  -- platform.valuations and deliberately so, because a number every screen shows should not
  -- depend on a second derivation being correct. arg_max takes the latest valuation, which is
  -- what the post-pass UPDATE in app/materialise.py did with DISTINCT ON.
  v.market_value AS value,
  -- THE WRAPPER ALLOWANCE. A (client, wrapper) fact repeated onto each of the group's accounts, so
  -- every reader takes max() per (contact, product) and NEVER sum() — a client with two ISAs shares
  -- one allowance, and summing reports double what they have used. The column comments on
  -- `platform.accounts` say the same thing, because this is the kind of rule that has to be true
  -- wherever somebody arrives.
  s.allowance_used,
  s.allowance_limit,
  s.allowance_remaining,
  CAST(s.allowance_tax_year AS VARCHAR) AS allowance_tax_year,
  -- The custodian's open date. SEI publishes it on every account; leaving it out of this
  -- projection stored NULL in platform.accounts.opened_at, so the record could not say when
  -- the account opened. A provider that has no such date publishes a typed NULL.
  CAST(s.opened_on AS DATE) AS opened_at,
  -- The account's own TERMS: what it is charged, and what money moves on it by standing
  -- arrangement. 0002 created all three columns and nothing had ever written to any of them, so
  -- the account record's Fees and Regular-payments cards read "none recorded" for every account in
  -- every book. Shaped by the provider's conformed layer (`derived/account_terms.sql`) rather than
  -- here, and cast to a JSON string for the same reason `data` is: the target is jsonb.
  CAST(to_json(s.fees) AS VARCHAR) AS fees,
  CAST(to_json(s.payments) AS VARCHAR) AS payments,
  CAST(to_json(s.withdrawals) AS VARCHAR) AS withdrawals,
  -- Already JSON (or NULL). to_json would quote the array, and the merge would
  -- then see no profiles and drop the ones it copied last time.
  CAST(s.bank_accounts AS VARCHAR) AS bank_accounts,
  s.source_updated_at,
  CAST(s.data AS VARCHAR) AS data
"""

_ACCOUNTS_FROM = """
FROM {rel[accounts]} AS s
LEFT JOIN (
  SELECT account_external_id, arg_max(market_value, as_of_date) AS market_value
    FROM {rel[valuations]} GROUP BY account_external_id
) AS v ON v.account_external_id = s.external_id
"""

_PORTFOLIOS = """
  {tenant_id} AS tenant_id,
  '{provider}' AS provider,
  CAST(s.external_id AS VARCHAR) AS external_id,
  CAST(s.account_external_id AS VARCHAR) AS account_external_id,
  CAST(s.name AS VARCHAR) AS name,
  CAST(s.portfolio_type AS VARCHAR) AS portfolio_type,
  CAST(s.purpose AS VARCHAR) AS purpose,
  -- NOT NULL in the book with a default of true, and an explicit NULL does not take a column's
  -- default (0067). So the conformed value is coalesced here: a provider that publishes no
  -- principal flag gets the book's own answer rather than failing the whole entity.
  coalesce(s.is_principal, true) AS is_principal,
  CAST(s.linked_external_id AS VARCHAR) AS linked_external_id,
  CAST(s.data AS VARCHAR) AS data
"""


_USERS = """
  {tenant_id} AS tenant_id,
  '{provider}' AS provider,
  CAST(s.external_id AS VARCHAR) AS external_id,
  CAST(s.email AS VARCHAR) AS email,
  CAST(s.first_name AS VARCHAR) AS first_name,
  CAST(s.last_name AS VARCHAR) AS last_name,
  CAST(s.phone AS VARCHAR) AS phone
"""


_VALUATIONS = """
  {tenant_id} AS tenant_id,
  CAST(s.account_external_id AS VARCHAR) AS account_external_id,
  s.as_of_date,
  '{provider}' AS source_system,
  s.market_value,
  s.cash_value,
  s.book_cost,
  s.unrealised_gain,
  s.opening_value,
  -- The crystallisation split. Morgan Lloyd computes the scheme's; Seccl states which
  -- portfolio group an account sits in; SEI states it on the Accounts report when the firm
  -- fills the columns in. Read here rather than left in `data` because drawdown eligibility
  -- turns on it. A reader prefers the administrator's split when that source has stated one,
  -- and does not add the two: they describe the same fund.
  --
  -- Every provider must PUBLISH the columns, as a typed NULL where it has no such concept: this
  -- projection is what `required_columns` reads, and a column the sink reads and a provider does
  -- not publish is a DuckDB Binder Error that aborts that whole tenant and lands in the ledger as
  -- "unavailable" — indistinguishable from "this firm has no data with this provider".
  s.crystallised_value,
  s.uncrystallised_value,
  coalesce(CAST(s.currency AS VARCHAR), 'GBP') AS currency,
  CAST(s.data AS VARCHAR) AS data
"""

_TRANSACTIONS = """
  {tenant_id} AS tenant_id,
  CAST(s.account_external_id AS VARCHAR) AS account_external_id,
  CAST(s.instrument_key AS VARCHAR) AS instrument_key,
  '{provider}' AS source_system,
  CAST(s.external_id AS VARCHAR) AS source_transaction_id,
  CAST(s.type AS VARCHAR) AS type,
  CAST(s.source_type AS VARCHAR) AS source_type,
  CAST(s.source_subtype AS VARCHAR) AS source_subtype,
  CAST(s.isin AS VARCHAR) AS isin,
  CAST(s.asset_name AS VARCHAR) AS asset_name,
  -- The amount expression is chosen in staging_sql. SEI shows the cash that moved: net, which on a
  -- buy is the gross plus charges and on a dividend is the gross less tax. A zero net is
  -- not a figure — interest, fees and FX publish gross as 0.0000 while the money is the
  -- net, and a stock transfer publishes both as 0 while the cost sits on book_value — so
  -- nullif steps past it. Other providers stay gross, then net. A row with none of them
  -- lands as 0, because the column is NOT NULL.
  {amount} AS amount,
  coalesce(CAST(s.currency AS VARCHAR), 'GBP') AS currency,
  s.quantity,
  s.unit_price AS price,
  s.net_amount AS settlement_amount,
  s.fee_amount,
  s.tax_amount,
  s.transaction_date,
  s.settlement_date,
  s.posted_at,
  CAST(s.status AS VARCHAR) AS status,
  -- What ties an order's legs together at the custodian (§3's `order_key`; Seccl's link/core id).
  -- The book has carried a `report_group` column since 0002 and NOTHING had ever written to it,
  -- so a twelve-line rebalance reached every surface as twelve unrelated trades on one day.
  CAST(s.order_key AS VARCHAR) AS report_group,
  -- The statement layer. Every one of these is already computed in the provider's conformed
  -- model and was selected away here, so a question with an answer upstream could not be asked
  -- downstream: what a trade actually COST (the dealing charge, folded into one `fee_amount`
  -- alongside the platform's monthly fee), when a dividend went EX, what a disposal REALISED, and
  -- what a payment did to the wrapper's allowance.
  --
  -- `report_group_kind` is the one that changes what can be built: it is the custodian's own
  -- statement bucket, which is what makes the headline breakdown a GROUP BY over the ledger
  -- rather than a second read of a vendor report. Beside the canonical `type`, never instead.
  --
  -- Named unconditionally, which puts the obligation on the CONFORMED LAYER: this select is
  -- shared by every provider, so a column named here must exist on every provider's relation or
  -- the Binder Error aborts that provider's whole transactions entity. That is the right place
  -- for it — §3 says a conformed entity publishes the column SET, and a provider that has not
  -- got a figure publishes a typed NULL and says why (see SEI's own transactions model). A
  -- tolerant "include it if the relation has it" would let a provider silently drop one.
  CAST(s.report_group_kind AS VARCHAR) AS report_group_kind,
  s.dealing_charges,
  s.other_charges,
  CAST(s.fee_type AS VARCHAR) AS fee_type,
  s.book_cost,
  s.realised_gain,
  s.trade_date,
  s.equalisation_payment,
  s.subscription_amount,
  s.source_updated_at,
  CAST(s.data AS VARCHAR) AS data
"""


# A model's wrappers arrive as a JSON ARRAY IN A STRING (`raw.models.product_type` is VARCHAR and
# Seccl sends `["GIA","ISA","JISA","PENSION"]`), a bare scalar, or nothing. try_cast to a list
# parses the first and returns NULL for the second, which is what makes the three-way coalesce
# faithful to what materialise.py's `_wrapper_names` did in Python.
_WRAPPER_LIST = """coalesce(
    try_cast(s.product_key AS VARCHAR[]),
    CASE WHEN nullif(trim(CAST(s.product_key AS VARCHAR)), '') IS NULL
         THEN NULL ELSE [CAST(s.product_key AS VARCHAR)] END,
    []
  )"""
#: The canonical wrappers a model is offered in, and — beside it — the ones that fell out of the
#: mapping, as text so the ledger can name them. Both parse in DuckDB; the observation downstream is
#: then a plain string comparison in Postgres.
_MODEL_PRODUCTS = f"""list_filter(
    list_transform({_WRAPPER_LIST}, x -> {_case("upper(x)", _TYPE_MAP)}),
    x -> x IS NOT NULL
  )"""

_UNMODELLED_WRAPPERS = f"""array_to_string(
    list_filter(
      list_transform({_WRAPPER_LIST},
                     x -> CASE WHEN {_case("upper(x)", _TYPE_MAP)} IS NULL THEN upper(x) END),
      x -> x IS NOT NULL),
    ', ')"""

#: Rendered into `_PRODUCTS` below, which is an f-string.
_pension_list = ", ".join(sql_str(w) for w in PENSION_WRAPPERS)

_PRODUCTS = f"""
  {{tenant_id}} AS tenant_id,
  {_PRODUCT_TYPE} AS product_type,
  -- A READABLE name and the wrapper CODE, which is what they each are. Discovery used to write the
  -- enum member into `name` and leave `code` NULL, so a firm's shelf listed "TPONBOND" as the
  -- product's NAME and carried no code at all -- the two the wrong way round.
  {_PRODUCT_LABEL} AS name,
  {_PRODUCT_TYPE} AS code,
  -- A discovered product is created ENABLED, because the firm demonstrably sells it: it has
  -- accounts in the wrapper.
  true AS enabled,
  -- Derived from the wrapper, not defaulted. See `PENSION_WRAPPERS`.
  ({_PRODUCT_TYPE} IN ({_pension_list})) AS is_pension,
  CAST(s.product_key AS VARCHAR) AS product_key
"""

# DISTINCT ON, not DISTINCT: two wrapper strings can map to one product_type, and merge_entity has
# no dedupe of its own -- two rows for one key would self-conflict inside the one INSERT.
_PRODUCTS_FROM = """
FROM (SELECT DISTINCT ON (product_key) * FROM (
        SELECT DISTINCT product_key FROM {rel[accounts]}
         WHERE product_key IS NOT NULL AND trim(CAST(product_key AS VARCHAR)) <> ''
      ) ORDER BY product_key) AS s
"""

_PRODUCT_PLATFORMS = f"""
  {{tenant_id}} AS tenant_id,
  {_PRODUCT_TYPE} AS product_type,
  -- The exact value the provider's API takes (Seccl's `wrapperType`). That is what makes the row
  -- actionable rather than descriptive: an account opened through us has to name the wrapper the
  -- way the custodian names it.
  CAST(s.product_key AS VARCHAR) AS external_id,
  {_PRODUCT_TYPE} AS external_name,
  -- WHAT THIS PROVIDER DOES FOR THE PRODUCT (0122), stamped on every insert rather than once by
  -- a backfill. 0122 set it for the rows that existed and nothing wrote it afterwards, so every
  -- mapping the pipeline has created since lands NULL -- and `GET /catalog/products` prefers
  -- `role = 'custodian'` when choosing which platform to name, so it was silently falling
  -- through to "any mapping" on exactly the rows a running pipeline produces. Interpolated from
  -- the provider's own declared role, not passed as a parameter, because interlace fingerprints
  -- a model from its SQL text: a role that changed would otherwise not rebuild.
  {{role}} AS role,
  true AS enabled,
  CAST(s.product_key AS VARCHAR) AS product_key
"""

_MODELS = f"""
  {{tenant_id}} AS tenant_id,
  CAST(s.external_id AS VARCHAR) AS external_id,
  -- NOT NULL in the book; a model the provider named nothing is still a model.
  coalesce(CAST(s.name AS VARCHAR), CAST(s.external_id AS VARCHAR)) AS name,
  CAST(s.model_type AS VARCHAR) AS model_type,
  CAST(s.aim AS VARCHAR) AS aim,
  CAST(s.risk_rating AS VARCHAR) AS risk_rating,
  s.model_charge,
  s.vatable,
  s.suspend_rebalances,
  s.cash_allocation,
  coalesce(CAST(s.status AS VARCHAR), 'active') AS status,
  {_MODEL_PRODUCTS} AS products,
  {_UNMODELLED_WRAPPERS} AS unmodelled_wrappers,
  -- Staged only so the ledger can count it: a model with no published allocation is not an error
  -- (the model still exists and accounts still name it), but it has no version to show either.
  len(coalesce(json_extract(s.allocations, '$[*]'), [])) AS allocation_count,
  s.source_updated_at,
  CAST(s.data AS VARCHAR) AS data
"""

_MODEL_PLATFORMS = """
  {tenant_id} AS tenant_id,
  CAST(s.external_id AS VARCHAR) AS external_id
"""


# The wrappers a fee package APPLIES to, from the ones the provider EXCLUDES. Seccl states the
# exclusion (`excludedWrapperTypes: ["ISA"]`) and platform.fee_packages stores the positive list, so
# the complement is taken here. Nothing excluded gives NULL, not all seven: NULL is `applies_to`'s
# own way of saying "no restriction", and materialising the full enum would freeze today's
# membership into every firm's fee register the day an eighth wrapper is added.
_ALL_TYPES = "[" + ", ".join(f"'{name}'" for name in PRODUCT_TYPES) + "]"
_EXCLUDED = _WRAPPER_LIST.replace("s.product_key", "s.excluded_products")
_APPLIES_TO = f"""CASE
    WHEN len({_EXCLUDED}) = 0 THEN NULL
    ELSE list_filter(
           {_ALL_TYPES},
           x -> NOT list_contains(
                  list_transform({_EXCLUDED}, y -> coalesce({_case("upper(y)", PRODUCT_ALIASES)},
                                                            upper(y))),
                  x))
  END"""

_FEE_PACKAGES = f"""
  {{tenant_id}} AS tenant_id,
  CAST(s.external_id AS VARCHAR) AS external_id,
  CAST(s.name AS VARCHAR) AS name,
  -- NOT NULL in 0002, and the provider is the only one with an opinion on it. 'unclassified'
  -- rather than a guess at 'platform': a category we could not read is a thing to notice in the
  -- register, not to quietly file.
  coalesce(nullif(trim(CAST(s.category AS VARCHAR)), ''), 'unclassified') AS category,
  coalesce(CAST(s.frequency AS VARCHAR), 'ongoing') AS frequency,
  CAST(s.fee_type AS VARCHAR) AS fee_type,
  CAST(s.rule AS VARCHAR) AS rule,
  CAST(s.charge_basis AS VARCHAR) AS charge_basis,
  CAST(s.payer AS VARCHAR) AS payer,
  coalesce(CAST(s.currency AS VARCHAR), 'GBP') AS currency,
  {_APPLIES_TO} AS applies_to,
  -- The provider's own word for it, which is rarely what the firm calls it.
  nullif(trim(CAST(s.fee_category_raw AS VARCHAR)), '') AS external_name,
  s.source_updated_at,
  CAST(s.data AS VARCHAR) AS data
"""

# The wrappers a ceding scheme can take, aliased but NOT filtered: the column is text[], not the
# enum, so a wrapper we do not model is still worth showing in the register.
_REGISTER_PRODUCTS = f"""list_transform(
    coalesce(try_cast(s.products AS VARCHAR[]), []),
    x -> coalesce({_case("upper(x)", PRODUCT_ALIASES)}, upper(x))
  )"""

_TRANSFER_PROVIDERS = f"""
  {{tenant_id}} AS tenant_id,
  CAST(s.name AS VARCHAR) AS name,
  -- The PROVIDER'S OWN ID is the identity, not the name (0046): P1 Investment Management Limited
  -- exists twice in Seccl's register, once reachable on Origo and once on ATG, as two master
  -- parties. Keyed on name, the second upsert overwrote the first and the firm lost a rail.
  CAST(s.external_id AS VARCHAR) AS masterparty_id,
  CAST(s.origo_id AS VARCHAR) AS origo_id,
  CAST(s.atg_reference AS VARCHAR) AS atg_reference,
  CAST(s.status AS VARCHAR) AS status,
  to_json(s.rails) AS rails,
  {_REGISTER_PRODUCTS} AS products,
  -- is_active is the conformed model's own reading of the status string, so the judgement lives in
  -- one place. Insert-only from here: an operator switching a scheme off keeps it off.
  s.is_active AS enabled,
  s.source_updated_at,
  CAST(s.data AS VARCHAR) AS data
"""

_THIRD_PARTY_PROVIDERS = f"""
  {{tenant_id}} AS tenant_id,
  CAST(s.name AS VARCHAR) AS name,
  -- DISCOVERED DISABLED, unlike products and unlike the transfer register. This is the platform's
  -- whole address book of third-party products; arriving enabled would put counterparties in front
  -- of an adviser as though the firm had approved them. The column's own default is true, so false
  -- has to be stated.
  false AS enabled,
  -- THE WRAPPER THIS PROVIDER PROVIDES (0130). The conformed model has published `product_key`
  -- all along -- its own comment says it exists "so a third-party provider can be attached to the
  -- product it actually provides" -- and this projection dropped it, so the only thing that ever
  -- populated the old join table was a column of admin checkboxes. Aliased like every other
  -- product read: Seccl says PENSION where the shelf says SIPP, and 2 of the dev book's 7 rows
  -- are that value.
  {_PRODUCT_TYPE} AS product,
  s.source_updated_at,
  CAST(s.data AS VARCHAR) AS data
"""

_FEE_PACKAGE_PLATFORMS = """
  {tenant_id} AS tenant_id,
  CAST(s.external_id AS VARCHAR) AS external_id,
  nullif(trim(CAST(s.fee_category_raw AS VARCHAR)), '') AS external_name,
  CAST(s.data AS VARCHAR) AS data
"""


# One row per allocation LINE, exploded out of the conformed model's JSON.
#
# Percentages arrive as FRACTIONS (0.245 for 24.5%, cash 0.05 for 5%) -- verified against staging --
# and platform.model_allocations stores 0-100 with a CHECK, so they are scaled here. Storing the
# fraction would pass the check and then price a model at a hundredth of its weight.
_MODEL_LINES = """
  {tenant_id} AS tenant_id,
  CAST(s.external_id AS VARCHAR) AS model_external_id,
  CAST(s.entry ->> 'assetId' AS VARCHAR) AS external_asset_id,
  NULL::VARCHAR AS isin,
  CAST(s.entry ->> 'assetName' AS VARCHAR) AS name,
  try_cast(s.entry ->> 'percentage' AS DECIMAL(12, 6)) * 100 AS percentage,
  s.idx AS sort_order,
  coalesce(s.cash_allocation, 0) * 100 AS cash_allocation,
  s.as_of_date,
  to_json(s.asset_mix) AS asset_mix
"""

# A withdrawn line stays in Seccl's payload with a non-Active status; carrying it would make the
# model total more than 100. A zero weight is not an allocation either.
#
# TWO PARALLEL UNNESTS, not a two-argument lambda. `list_transform(l, (x, i) -> …)` works in DuckDB
# and is what this was, but interlace parses every model with sqlglot and re-renders it, and the
# round-trip turns `(x, i) ->` into `main."row"(x, i) ->` — a Binder Error at APPLY time, which no
# plan and no direct-DuckDB test would show. DuckDB unnests two lists in lockstep, so a scalar
# `range(len(…))` supplies the index without a lambda, and this form survives the round-trip.
_MODEL_LINES_FROM = """
FROM (
  SELECT external_id, cash_allocation, as_of_date, asset_mix,
         unnest(json_extract(allocations, '$[*]')) AS entry,
         unnest(range(len(json_extract(allocations, '$[*]')))) AS idx
    FROM {rel[models]}
) AS s
WHERE lower(coalesce(s.entry ->> 'status', 'Active')) = 'active'
  AND try_cast(s.entry ->> 'percentage' AS DECIMAL(12, 6)) > 0
"""

# The provider's standing note, which arrives in three shapes and must not be assumed to be one.
# Seccl publishes it as PLAIN FREE TEXT -- `json_extract_string(commentary, '$')` on the real
# warehouse raises "Malformed JSON at byte 0" and fails the whole entity, because `This is a sample
# commentary about the ETC` is not JSON. A firm may equally have a JSON-encoded string there, and
# the conformed column is typed `json` in Seccl's model, so both have to read.
#
# `json_type` is the discriminator, and the OBJECT/ARRAY branch is app/materialise.py's
# `isinstance(str)` test: a structured value is not somebody's note, and writing its serialisation
# into a markdown body would put raw JSON in front of a client. Not `json_extract_string` alone --
# at '$' that returns an object's own text rather than NULL, so it does not make that distinction.
_MODEL_COMMENTARY = """
  {tenant_id} AS tenant_id,
  CAST(s.external_id AS VARCHAR) AS model_external_id,
  s.body
"""

# Resolved once in the subquery, so the projection and its filter cannot come to disagree about what
# counts as a note. Written out in full rather than interpolated from a shared constant: that made
# the whole projection an f-string, ruff then wanted a `noqa: S608` marker on the assignment,
# and on a triple-quoted line the comment lands INSIDE the literal and reaches DuckDB as SQL.
_MODEL_COMMENTARY_FROM = """
FROM (
  SELECT external_id,
         nullif(trim(
           CASE WHEN NOT json_valid(commentary) THEN CAST(commentary AS VARCHAR)
                WHEN json_type(commentary) = 'VARCHAR' THEN json_extract_string(commentary, '$')
                WHEN json_type(commentary) IN ('OBJECT', 'ARRAY') THEN NULL
                ELSE CAST(commentary AS VARCHAR) END), '') AS body
    FROM {rel[models]}
) AS s
WHERE s.body IS NOT NULL
"""


#: The entities this path merges, in dependency order.
CONFORMED: tuple[Entity, ...] = (
    Entity(
        source="nodes",
        target="platform.nodes",
        select=_ORGS,
        key=("tenant_id", "provider", "key"),
        # Provider-owned in full: an org unit is the custodian's own structure and no Graphene
        # surface edits one, so there is no local edit for a re-parenting upstream to lose.
        updatable=("*",),
        row_key="key",
    ),
    Entity(
        source="instruments",
        target="platform.instruments",
        select=_INSTRUMENTS,
        link_type="instrument",
        adopt_on=("isin",),
        # Nothing in `updatable`: an instrument's typed columns are set once from what the
        # provider first published, and the payload lives on links.data. An upstream fund
        # RENAME therefore does not reach the book.
        # `ocf` is fill-if-null. The charge file arrives beside the asset, mostly blank, and
        # two custodians can share an ISIN. A nightly NULL must not wipe a figure already stored.
        fill_if_null=("ocf",),
    ),
    # The firm's shelf, DISCOVERED from the accounts it actually holds. Products are the one
    # catalogue entity a firm should not have to type in: the custodian already knows which wrappers
    # the firm operates, and the reference its API expects for each one is the same string.
    Entity(
        source="accounts",
        name="products",
        target="platform.products",
        select=_PRODUCTS,
        from_sql=_PRODUCTS_FROM,
        key=("tenant_id", "product_type"),
        # The partial index's predicate: three pre-0044 rows still carry a NULL tenant_id, so the
        # key is partial and has to be named to be inferred.
        conflict_where="tenant_id IS NOT NULL",
        # INSERT-ONLY. Discovery decides a product EXISTS; whether the firm offers it (`enabled`)
        # and what the firm calls it (`name`) are the firm's own, and re-asserting either would turn
        # every nightly run into an override of those decisions.
        updatable=(),
        requires=(
            Requirement(
                check="s.product_type IS NOT NULL",
                reason=("'unmodelled wrapper: ' || coalesce(s.product_key, '(null)')"),
            ),
        ),
        row_key="product_key",
    ),
    # Each platform's own reference for the same shelf item. This is what makes a product row
    # actionable rather than descriptive.
    #
    # `updatable` is ONLY `external_id` on purpose. `account_open_reference` is the id a write
    # SENDS to open an account (SEI accountTemplateId / Seccl schemeProductId), set by an
    # operator on the admin products page. The pipeline READS `external_id`. If the merge
    # owned the opening template it would blank it every night.
    Entity(
        source="accounts",
        name="product_platforms",
        target="platform.product_platforms",
        select=_PRODUCT_PLATFORMS,
        from_sql=_PRODUCTS_FROM,
        key=("product_id", "provider"),
        updatable=("external_id",),
        # A template save used to insert this row with a null role. The journey only
        # offers `role = 'custodian'`, so the product stayed unconnected. A later
        # merge fills that null from the provider's declared role and does not
        # replace a role the firm has set.
        fill_if_null=("role",),
        parents=(
            Parent(
                column="product_id",
                source="product_type",
                via="unique:platform.products:product_type",
                required=True,
            ),
            Parent(column="provider", source="", via="platform", required=True),
        ),
        row_key="product_key",
    ),
    Entity(
        source="models",
        target="platform.models",
        select=_MODELS,
        link_type="model",
        # The type arrives after the first sighting, once the assignment read has run.
        # A type the firm has since set is not NULL, and is left alone.
        fill_if_null=("model_type",),
        # The first sighting of an unnamed model stores its id as the name. The assignment
        # read replaces that placeholder and does not replace a name the firm has typed.
        replace_placeholder=("name",),
        parents=(Parent(column="provider", source="", via="platform"),),
        observes=(
            Requirement(
                # Cast explicitly: `allocation_count` exists on no target, so the merge's cast list
                # -- which is built from the target's own column types -- never touches it, and
                # whether the landing table typed it as a bigint or as text depends on the delivery.
                check="coalesce(cast(s.allocation_count AS integer), 0) > 0",
                reason="'no allocations published'",
            ),
            Requirement(
                check="s.unmodelled_wrappers = ''",
                # Counted, not rejected: Seccl's models list SSAS alongside four wrappers our enum
                # does have, which does not invalidate the model -- but its arrival is how we find
                # out the enum needs extending.
                reason="'unmodelled model wrapper: ' || s.unmodelled_wrappers",
            ),
        ),
    ),
    Entity(
        source="models",
        name="model_allocations",
        # Documented rather than used: the procedure owns the write, because a version's key is
        # `max(version) + 1` and no staged column can carry that.
        target="platform.model_versions",
        select=_MODEL_LINES,
        from_sql=_MODEL_LINES_FROM,
        procedure="platform.merge_model_versions",
        row_key="model_external_id",
    ),
    Entity(
        source="models",
        name="model_commentary",
        target="platform.model_commentary",
        select=_MODEL_COMMENTARY,
        from_sql=_MODEL_COMMENTARY_FROM,
        procedure="platform.merge_model_commentary",
        row_key="model_external_id",
    ),
    Entity(
        source="models",
        name="model_platforms",
        target="platform.model_platforms",
        select=_MODEL_PLATFORMS,
        key=("model_id", "provider"),
        updatable=("external_id",),
        parents=(
            Parent(column="model_id", source="external_id", via="link:model", required=True),
            Parent(column="provider", source="", via="platform", required=True),
        ),
    ),
    # The firm's priced charge structures. DISCOVERED ENABLED, like products and unlike the provider
    # registers: a fee structure exists because someone at the firm priced it, so arriving switched
    # off would hide the register the sync just found. `enabled` is not staged at all, so the
    # column's own default lands it on and no run can ever change it back.
    Entity(
        source="fee_packages",
        target="platform.fee_packages",
        select=_FEE_PACKAGES,
        key=("tenant_id", "external_id"),
        conflict_where="external_id IS NOT NULL",
        # The pricing IS the provider's statement: it is configured at the custodian, so a change
        # there is the truth. Enumerated rather than `{*}` for one reason -- `enabled` must not be
        # in it, and a firm's product/model scoping is set once rather than re-asserted.
        updatable=(
            "name",
            "category",
            "frequency",
            "fee_type",
            "rule",
            "charge_basis",
            "payer",
            "currency",
            "applies_to",
            "data",
        ),
        # No product or model parent: Seccl scopes a fee structure by excluded WRAPPER and its
        # conformed model publishes neither key. platform.fee_packages keeps both columns for a
        # provider that does scope that way, and they stay NULL until one does.
        requires=(
            Requirement(
                check="s.external_id IS NOT NULL AND s.name IS NOT NULL",
                reason="'no external id or name'",
            ),
            # `rule` is what prices an invoice, and a rule stored under the wrong shape passes its
            # CHECK and then bills the wrong amount -- the same reason an unmapped transaction type
            # is rejected rather than filed as 'adjustment'.
            Requirement(
                check="s.fee_type IS NOT NULL AND s.rule IS NOT NULL",
                reason=(
                    "'unmapped fee shape: ' || coalesce(s.external_name, '?')"
                    " || ' (' || coalesce(s.name, '?') || ')'"
                ),
            ),
        ),
    ),
    Entity(
        source="fee_packages",
        name="fee_package_platforms",
        target="platform.fee_package_platforms",
        select=_FEE_PACKAGE_PLATFORMS,
        key=("fee_package_id", "provider"),
        updatable=("external_id", "external_name", "data"),
        parents=(
            Parent(
                column="fee_package_id",
                source="external_id",
                via="unique:platform.fee_packages:external_id",
                required=True,
            ),
            Parent(column="provider", source="", via="platform", required=True),
        ),
    ),
    # The ceding schemes a transfer can be addressed to. Discovered ENABLED: the register is the
    # custodian's list of schemes an instruction can actually name, and arriving switched off meant
    # every new transfer failed to find its ceding provider until someone enabled it by hand.
    Entity(
        source="transfer_providers",
        target="platform.transfer_providers",
        select=_TRANSFER_PROVIDERS,
        key=("tenant_id", "masterparty_id"),
        conflict_where="masterparty_id IS NOT NULL",
        updatable=(
            "name",
            "origo_id",
            "atg_reference",
            "status",
            "rails",
            "products",
            "data",
        ),
        requires=(
            Requirement(
                check="s.name IS NOT NULL AND s.masterparty_id IS NOT NULL",
                reason="'no name or provider id'",
            ),
        ),
        row_key="masterparty_id",
    ),
    Entity(
        source="third_party_providers",
        target="platform.third_party_providers",
        select=_THIRD_PARTY_PROVIDERS,
        key=("tenant_id", "name"),
        updatable=("data",),
        requires=(Requirement(check="s.name IS NOT NULL", reason="'no name'"),),
        row_key="name",
    ),
    Entity(
        source="clients",
        target="crm.contacts",
        select=_CLIENTS,
        link_type="contact",
        # The payload stays on the LINK. platform.{accounts,instruments,models} each keep their own
        # `data` because app/materialise.py has always written it there and it has readers; contacts
        # is the one place the two paths disagreed, and NULL is what the old path leaves.
        exclude=("data",),
        # INSERT-ONLY apart from these: name and email are the firm's once created (a person's
        # name follows their parts, 0239, whoever edits them). The
        # custodian's status, base currency, and kind are the provider's to refresh.
        # Kind is type plus sub_type: SEI sends a code (5 Individual, 6 Organization, and
        # an organisation type of trust, charity or company). Leaving kind insert-only
        # kept every SEI client as the organisation the first, untranslated, load wrote.
        # A staff edit of kind on a synced contact is replaced at the next merge.
        updatable=("provider_status", "currency", "type", "sub_type"),
        # A title that is still the custodian's code (`1`, `21`) is replaced with the
        # resolved courtesy title. A word the firm has typed is not a number, and stays.
        replace_numeric=("title",),
        # A blank email, phone or NINO is filled when a later drop has one. A value the
        # firm has typed is left alone — these are not in `updatable`.
        fill_if_null=(
            "email",
            "mobile",
            "phone",
            "national_identifier",
            "national_identifier_type",
            # The custodian's copy of a fact the firm can also type. A blank is filled.
            # A value the firm has set is left alone — these are not in `updatable`.
            "gender",
            "marital_status",
            "is_vulnerable",
            "country_of_birth",
            "fatca_status",
            "us_person",
            "passport_number",
            "passport_country",
        ),
        parents=(Parent(column="node_id", source="key", via="node"),),
    ),
    Entity(
        source="accounts",
        target="platform.accounts",
        select=_ACCOUNTS,
        from_sql=_ACCOUNTS_FROM,
        reads=("valuations",),
        link_type="account",
        # `value`, the allowance, currency, open date and the three TERMS columns are the
        # provider's to state on every run; everything else on an account is the firm's
        # once created. `bank_accounts` is not in this list: SEI pay-to profiles are
        # refreshed by `_merge_sei_bank_accounts`, which leaves entries the firm typed.
        updatable=(
            "value",
            "currency",
            "allowance_used",
            "allowance_limit",
            "allowance_remaining",
            "allowance_tax_year",
            # The custodian's open date, like the balance: a date we missed on the first
            # sighting, or one SEI later corrects, has to land. Nothing in the app writes it.
            "opened_at",
            "fees",
            "payments",
            "withdrawals",
        ),
        parents=(
            Parent(column="contact_id", source="client_external_id", via="link:contact"),
            Parent(column="node_id", source="key", via="node"),
            # The wrapper the account is held in, and the model it is managed to. Both were NULL for
            # the whole book before: `_sync_products`' key->id map was computed and discarded, and
            # `_upsert_account` had no model_id parameter at all -- which is what made
            # POST /crm/accounts/{id}/drawdown-session answer "pension accounts only" for every real
            # SIPP in the book. Not required: an account naming a wrapper we could not map keeps a
            # NULL rather than a guess, and the next run resolves it.
            Parent(
                column="product_id",
                # `product_type`, not `product_key`: the target column holds the CANONICAL type and
                # the provider's raw wrapper string does not match it for any aliased wrapper.
                source="product_type",
                via="unique:platform.products:product_type",
            ),
            Parent(column="model_id", source="model_key", via="link:model"),
        ),
    ),
    Entity(
        source="portfolios",
        target="platform.portfolios",
        select=_PORTFOLIOS,
        # A NATURAL key, not a link: SEI's PORTFOLIO_ID is globally unique and belongs to exactly
        # one account (zero of the drop's 1,163 appear under two), so the provider's own id is the
        # identity and there is nothing for a `platform.links` row to add.
        key=("tenant_id", "provider", "external_id"),
        updatable=(
            "name",
            "portfolio_type",
            "purpose",
            "is_principal",
            "linked_external_id",
            "data",
        ),
        parents=(
            # REQUIRED. A portfolio is reached THROUGH its account — it is the second half of the
            # address a SEI money instruction takes — so one whose account did not materialise is a
            # counted reject rather than a row nothing can use.
            Parent(
                column="account_id",
                source="account_external_id",
                via="link:account",
                required=True,
            ),
        ),
        row_key="external_id",
    ),
    Entity(
        source="users",
        # `auth.users`, NOT a `platform.*` table, and therefore NOT the generic merge policy —
        # see `procedure` below. Declared here anyway so it goes through the same staging chain,
        # the same slice predicate and the same ledger line as every other entity: a firm's staff
        # at a custodian is one more thing the pipeline delivers, and giving it its own path would
        # be a second way to reach the book.
        target="auth.users",
        select=_USERS,
        # NO `key`, deliberately, and the invariant is that an entity declares exactly ONE of a
        # natural key / a link / a procedure — with two the ledger cannot say which ran. A first
        # cut declared both and `test_pipeline_coverage` caught it. Identity here is not
        # expressible as a key anyway: `auth.users` has a uuid PK the provider never sees and its
        # uniqueness is `(tenant_id, email)`, which is the thing the procedure DECIDES about
        # rather than something it can look up.
        #
        # THE ONE SHAPE THE POLICY CANNOT EXPRESS, which is what `procedure` is for (0068 wrote it
        # for model versions). The generic merges insert-then-refresh on a key; this entity must
        # decide between ATTACHING the provider's id to a login that already exists under the same
        # email and INSERTING a disabled placeholder — a choice, per row, against a table the
        # pipeline holds no privilege on. `auth.merge_provider_user` (0123) is that decision,
        # SECURITY DEFINER so `book_writer` gets exactly those statements and not the table.
        procedure="auth.merge_provider_user",
        # No `updatable` either: the procedure decides what it owns PER ROW — the provider owns
        # `provider_refs` and nothing else on a real WorkOS login, and the display fields too on a
        # placeholder it created — and a list here would be a second, coarser answer to the
        # question that matters most about this entity.
        # AN ADDRESS IS THE ENTRY CONDITION, and it is filtered in the FROM rather than declared as
        # a `Requirement`, which is the one place this entity departs from every other.
        #
        # The drop carries the POPULATION and the API carries the EMAIL, so an employee SEI holds
        # no address for is a real and ordinary state rather than a fault — and there is nothing to
        # decide about one: it can neither attach (nothing to match on) nor insert
        # (`auth.users.email` is NOT NULL), and a synthetic address is out of the question because
        # that column is what a login is addressed by and what the send guard reads.
        #
        # A `Requirement` would file one `platform.merge_rejects` row per such person PER RUN,
        # for ever. The class docstring's objection to filtering — "a row silently filtered out is
        # indistinguishable from a row the provider never sent" — does not apply here, because the
        # row is not silent: `platform.users` publishes every employee including these, with the
        # email NULL, and it reaches the firm's own slice. So "how many of this firm's SEI staff
        # cannot be mapped" is a question about the CURRENT rows — `SELECT count(*) FROM
        # tenant_{uid}.users WHERE email IS NULL` — which is the same reasoning that keeps
        # `platform.reconciliation_breaks` unbuilt: a stored copy of a derivable state goes stale
        # the moment either side is corrected, and here it would go stale nightly and loudly.
        from_sql=("FROM (SELECT * FROM {rel[users]} WHERE email IS NOT NULL AND email <> '') AS s"),
        row_key="external_id",
    ),
    Entity(
        source="valuations",
        target="platform.valuations",
        select=_VALUATIONS,
        key=("account_id", "as_of_date", "source_system"),
        updatable=("*",),
        parents=(
            Parent(
                column="account_id",
                source="account_external_id",
                via="link:account",
                required=True,
            ),
        ),
        requires=(
            Requirement(
                check="s.market_value IS NOT NULL AND s.as_of_date IS NOT NULL",
                reason="'no value or date'",
            ),
        ),
        row_key="account_external_id",
    ),
    Entity(
        source="transactions",
        target="platform.transactions",
        select=_TRANSACTIONS,
        # Two tenants may sync the same provider transaction: staging points both at one SEI
        # firm. The row is that firm's (RLS, its own account), so the key includes the tenant.
        # A later sync updates this firm's copy and leaves the other firm's row alone.
        key=("tenant_id", "source_system", "source_transaction_id"),
        # Provider-owned columns are replaced. `adviser_note` and `expectation_id` are omitted
        # from `updatable` so a nightly merge does not wipe a note or a match we wrote.
        #
        # ENUMERATED rather than `{*}`. `account_id` stays as inserted: a re-sync must not
        # retarget the row onto another account. `instrument_id` is left out because the merge
        # has no coalesce: a run that could not resolve the instrument would blank a link an
        # earlier run established.
        updatable=(
            "type",
            "status",
            "amount",
            "quantity",
            "price",
            "settlement_amount",
            "fee_amount",
            "tax_amount",
            "transaction_date",
            "settlement_date",
            "posted_at",
            "currency",
            "isin",
            "asset_name",
            "source_type",
            "source_subtype",
            "report_group",
            "report_group_kind",
            "dealing_charges",
            "other_charges",
            "fee_type",
            "book_cost",
            "realised_gain",
            "trade_date",
            "equalisation_payment",
            "subscription_amount",
            "data",
        ),
        parents=(
            Parent(
                column="account_id",
                source="account_external_id",
                via="link:account",
                required=True,
            ),
            Parent(column="instrument_id", source="instrument_key", via="link:instrument"),
        ),
        requires=(
            # The conformed layer leaves `type` NULL when the provider's taxonomy did not map.
            # Filing money under a guessed type is worse than not filing it, so this is a visible
            # reject the mapping can be extended for -- 117 of them on the dev book today, which is
            # the number Phase 5's parity fixture asserts.
            Requirement(
                check="s.type IS NOT NULL",
                reason=(
                    "'unmapped type: ' || coalesce(s.source_type, '?')"
                    " || '/' || coalesce(s.source_subtype, '?')"
                ),
            ),
        ),
        row_key="source_transaction_id",
    ),
)


#: What each provider publishes today. A provider that has not conformed an entity simply has no
#: model for it, which is not an error -- Morgan Lloyd has no accounts in the custodian sense.
PROVIDER_ENTITIES: dict[str, tuple[str, ...]] = {
    # The SOURCE relations, not the entity names: `products` and `product_platforms` are both read
    # from `accounts`, and a provider that publishes accounts gets both.
    "seccl": (
        "nodes",
        "instruments",
        "accounts",
        "models",
        "fee_packages",
        "transfer_providers",
        "third_party_providers",
        "clients",
        "valuations",
        "transactions",
    ),
    # ML publishes clients, and now VALUATIONS -- but not as a second opinion on what an account
    # is worth. For a SIPP it is the scheme ADMINISTRATOR, not the custodian: we PUSH it the
    # valuation (`cam_createpolicyvaluation`) and it answers with the crystallisation split it
    # computes plus the SCHEME's own bank balance, which is cash the client holds at a second
    # place and cannot presently see anywhere (0117).
    #
    # Its transfers and beneficiaries still do NOT merge: their book tables' lifecycle is
    # Graphene's, and a stale nightly vendor read could walk a transfer backwards past 0056's
    # monotonic rule.
    #
    # SAFE AGAINST INVENTING ACCOUNTS, and that is a property of the entity rather than a promise
    # here: `valuations` resolves `account_id` via `link:account` with `required=True`, so a
    # Dynamics scheme nobody created through `sync_client` has no link, becomes a counted row in
    # `platform.merge_rejects`, and never becomes a book account.
    "morgan-lloyd": ("clients", "valuations"),
    # SEI publishes six of the conformed relations. Its slice still goes through `predicate.sei()`
    # rather than lineage containment: the enablement is the firm/subfirm/DSR triple, and
    # containment would turn firm-AND-subfirm-AND-team into OR, widening any multi-axis slice.
    #
    # `models` IS here now (it was not, and `model_key` therefore resolved against nothing for the
    # 85% of SEI accounts that name one). `fee_packages` is the firm's PERIODIC fee packages
    # (feePackageProfileId), conformed from FeePackageRule; SEI's real-time and one-time fee types
    # are separate axes not conformed here. Registering a relation the provider does not publish is
    # a Binder Error that aborts the tenant, so list a relation here only once its model exists.
    "sei": (
        "nodes",
        "instruments",
        # The Reference report's ceding counterparties (transfer agents, custodians, cash
        # agents, tax-wrap providers). Global — firm_id is null — so the slice is not narrowed.
        "transfer_providers",
        "accounts",
        # The container BELOW an account, which four SEI write paths take a `portfolioId` for.
        # SEI alone: Seccl has nothing below an account and models the same split as a second one.
        "portfolios",
        "clients",
        "valuations",
        "transactions",
        # The investment models its accounts name, DISCOVERED from those accounts because SEI's own
        # two model reports (`ModelsIntExt`, `ModelAlloc`) are declared and 0 rows on all 74 drops.
        # Registering this registers FOUR entities — the model, `model_platforms`,
        # `model_allocations` and `model_commentary` all read this one relation — and the conformed
        # model publishes what SEI does not say as TYPED nulls so none of them hits a Binder Error.
        # Without it `platform.accounts.model_key` (85% populated, 49 distinct) resolved against
        # nothing.
        "models",
        # The firm's own STAFF at SEI, merged into `auth.users` through `auth.merge_provider_user`
        # rather than the generic policy. SEI alone for now because it is the only custodian in the
        # estate that publishes an employee-user population at all: Seccl's `userType` enum is only
        # `Machine | User` and access there is the NODE TREE rather than a person link, and Morgan
        # Lloyd exposes one `cam_FinancialAdviserContact` for the whole firm.
        "users",
        # The firm's PERIODIC fee packages (ongoing adviser / platform / DFM charges), keyed on the
        # SEI feePackageProfileId. Firm-scoped — narrowed on firm_id, no subfirm/DSR axis — so it is
        # in _SEI_NO_SUBFIRM_TABLES / _SEI_NO_DSR_TABLES in predicate.py.
        "fee_packages",
    ),
}

#: Relations projected into the tenant's own lake but NOT merged into the book: holdings are read
#: from the lake by every surface that shows them, so `positions` has no OLTP table to merge into.
LAKE_ONLY: dict[str, tuple[str, ...]] = {
    # Holdings are read from the lake by every surface that shows them, so `positions` has no OLTP
    # table to merge into.
    "seccl": ("positions",),
    "morgan-lloyd": ("transfers", "beneficiaries"),
    # SEI's holdings, for the same reason as Seccl's. It was EMPTY, so a firm custodied at SEI had
    # no `tenant_{uid}.positions` at all — every display read returned nothing and the client
    # portal showed an empty holdings table, an empty allocation donut and no cash beside an OLTP
    # `value` that is real, which is indistinguishable from owning nothing.
    #
    # Projecting it was HALF the fix and would have been worse on its own: SEI's conformed
    # `positions` published six of the fifteen columns `app/lake/holdings.py::_HOLDINGS_SQL`
    # reads, so the read was a Binder Error rather than an empty result — an empty page turned
    # into an unavailable one. The model was conformed first.
    "sei": ("positions",),
}


def for_provider(provider: str) -> tuple[Entity, ...]:
    published = PROVIDER_ENTITIES.get(provider, ())
    return tuple(entity for entity in CONFORMED if entity.source in published)


def lake_relations(provider: str) -> tuple[str, ...]:
    """Every relation this provider projects: what the merged entities read, plus lake-only ones.

    De-duplicated, because two entities can read one relation -- the shelf and each platform's
    reference for it both come from `accounts`.
    """
    seen: dict[str, None] = {}
    for entity in for_provider(provider):
        for relation in entity.relations():
            seen[relation] = None
    for relation in LAKE_ONLY.get(provider, ()):
        seen[relation] = None
    return tuple(seen)
