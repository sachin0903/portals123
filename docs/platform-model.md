# The canonical platform model — review and rebuild

> **Reading note (Aug 2026).** `app/fanout.py`, `app/fanout_listener.py` and `app/materialise.py`
> are **deleted**. They are named throughout the sections below because those sections record the
> reasoning that produced the current design, and rewriting the history would lose it — but every
> present-tense sentence about "the fan-out" or "the materialiser" describes work now done inside
> each provider's own interlace DAG by `pkgs/book-sink`. §4 marks the supersession; see
> `docs/provider-pipeline.md` for what replaced them.

**Status:** step 1-3 built (Seccl end-to-end); SEI and Morgan Lloyd still to conform. Extends
`data-architecture.md` (Decisions 1, 2, 4) with the field-level detail those decisions deferred,
and answers "should the sync be a Python sink?".

The ask: one consolidated set of canonical OLTP models covering clients, accounts, products,
models, assets, transactions, firms/orgs and valuations; each linked to its lake counterpart
through `platform.links` with the full provider payload in `data`; populated
automatically from `services/{seccl,sei,morgan-lloyd}`, creating the record and the link when
they do not exist.

---

## 1. What exists today

**OLTP (`0002_platform_crm`, `0003_platform_links`).** More is in place than the brief assumes:

| Concept        | Table                                     | State                                                            |
| -------------- | ----------------------------------------- | ---------------------------------------------------------------- |
| Client         | `crm.contacts`                            | full — Decision 8: the contact _is_ the client                   |
| Account        | `platform.accounts`                       | full, incl. `value`, `performance` jsonb, `data` jsonb           |
| Product        | `platform.products`                       | full (`platform.product_type` enum)                              |
| Model          | `platform.models`                         | full, but no holdings/allocations and no per-platform join       |
| Fee package    | `platform.fee_packages`                   | full                                                             |
| Transaction    | `platform.transactions`                   | full, incl. identity plus the statement facts (0242)             |
| Provider link  | `platform.links`                          | full — polymorphic, audited, RLS'd, already has `data jsonb`     |
| **Instrument** | —                                         | **missing** (Decision 1 deferred it)                             |
| **Org unit**   | —                                         | **missing** — lives as opaque jsonb in `tenant_providers.config` |
| **Valuation**  | `platform.accounts.value` + `performance` | **not a time series** — no per-date history                      |
| Holding        | — (lake only)                             | deliberate; the portals read holdings through `app/lake`         |

So this is not a green field. The rebuild is: **add the four missing entities, and make the
provider layers agree field-for-field** — the part that was never actually done.

**Lake (`platform.*` in each provider's DuckLake catalogue).** All three publish a consumption
layer, and SEI's models say in comments that they follow Seccl's naming so "the two custodians
present the same concepts under the same names". They do not. What is actually published:

| Entity       | SEI                                                                                                        | Seccl                                                                               | Morgan Lloyd                                               |
| ------------ | ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| clients      | `client_id`, `client_type`, `first_name`, `surname`, `date_of_birth`, `address_lines[]`, tenancy triple    | Seccl API shape, snake_cased (`id`, `firm_id`, …), whole record                     | `raw.contacts` passthrough (Dynamics `contactid`, `cam_*`) |
| accounts     | `account_number`, `account_type`, `account_status`, `opened_on`, `strategy_id`                             | none — accounts only exist inside `positions`/`transactions` enrichment             | none (scheme members)                                      |
| positions    | `taxlot_id`, `quantity`, `price`, `market_value`, `cost`, `accrued_interest`                               | `quantity`, `book_value`, `current_value`, `growth`, `allocation`                   | none                                                       |
| transactions | `transaction_id`, `net_amount`, `gross_amount`, `fee_amount`, `tax_amount`, signed via `DEBIT_CREDIT_FLAG` | `amount`, `consideration`, `dealing_charges`, `other_charges`, `origin`, `fee_type` | none                                                       |
| assets       | `instrument_id`, `isin`, `sedol`, `cusip`, `asset_type`                                                    | inline on positions (`asset_id`, `asset_name`, `isin`)                              | none                                                       |
| org units    | `firms`, `subfirms`, `dsr_teams`, `dsr_grants`, `tenancy`                                                  | `nodes` (recursive `lineage[]`, `level`)                                            | none (`ml_tenant_id`)                                      |
| valuations   | none (derivable from `positions`)                                                                          | on positions (`current_value`, `opening_value`, `growth`)                           | `valuation_runs` — a _push ledger_, not a valuation        |

### The inconsistencies, concretely

1. **Identity.** `account_number` (SEI) vs `id` (Seccl) vs `contactid` (ML). Nothing carries a
   uniform `external_id`, so every consumer special-cases the provider.
2. **Tenancy.** SEI publishes a triple (`firm_id`, `subfirm_id`, `dsr_team_ids` LIST) on every
   entity; Seccl publishes `node_id` + a recursive `node_lineage[]`; ML publishes `ml_tenant_id`.
   `app/fanout.py` carries the cost of this: a per-provider predicate builder, plus
   `_sei_predicate`'s shape-awareness (`_SEI_NO_SUBFIRM_TABLES`, `_SEI_SCALAR_DSR_TABLES`) that
   exists only because the dimension tables carry different axes from the entities.
3. **Money.** SEI unsigns amounts and pairs each with a `DEBIT_CREDIT_FLAG` (re-signed in the
   model); Seccl sends signed doubles, sometimes as strings, so its transaction model `try_cast`s
   both sides of every `coalesce`. No provider publishes a decimal type.
4. **Dates.** SEI's raw layer declares real `DATE`/`TIMESTAMP` (`sei_sync/schema.py`); Seccl's
   dates are strings on the wire and stay strings through the platform layer
   (`current_price_date`, `transaction_date`).
5. **Vocabularies.** SEI status/type fields are numeric codes joining `ref.codes`; Seccl uses
   English words (`Active`, `Pending`, `Order`). Neither is mapped to a canonical enum.
6. **Grain claims.** SEI's models now publish one row per entity with deterministic picks and
   document it; Seccl's `platform.*` are views with no `key` declared at all.

Point 6 matters most for a sync: **you cannot upsert safely from a relation whose grain is
undeclared.**

---

## 2. First principles: what is canonical, and where it lives

Two rules decide every table below.

**Rule A — OLTP holds identity, relationships and anything a user edits or a decision depends
on. The lake holds facts at provider grain, and anything time-series or high-volume.** The lake
is rebuildable from the raw zone; OLTP is not.

**Rule B — one canonical row per real-world thing, however many providers describe it.** An
account held at both SEI and Seccl is one `platform.accounts` row with two links. This is
Decision 1, and it is why the canonical model must not carry a `provider` column on the entity.

Applying them:

| Entity        | Home                                  | Grain                       | Why                                                                                               |
| ------------- | ------------------------------------- | --------------------------- | ------------------------------------------------------------------------------------------------- |
| Org unit      | **OLTP** `platform.nodes` (new)       | provider org unit           | It authorises what a tenant sees. Opaque jsonb config is not something to hang authorisation on.  |
| Client        | **OLTP** `crm.contacts`               | person/entity               | Edited by staff, owns relationships.                                                              |
| Account       | **OLTP** `platform.accounts`          | logical account             | Edited, and the join point for everything.                                                        |
| Product       | **OLTP** `platform.products`          | wrapper per platform        | Configuration.                                                                                    |
| Model         | **OLTP** `platform.models`            | investment model            | Configuration; discretionary flag hangs off it.                                                   |
| Instrument    | **OLTP** `platform.instruments` (new) | security                    | Small, shared, and referenced by name from three places today (`isin` strings on transactions).   |
| Fee package   | **OLTP** `platform.fee_packages`      | fee structure               | Configuration.                                                                                    |
| Transaction   | **OLTP** `platform.transactions`      | one movement                | Decision 2 — first-class fact; matched against expectations.                                      |
| **Valuation** | **OLTP** `platform.valuations` (new)  | account × date              | One row per account per day is small, and every portal surface wants trend without a lake read.   |
| **Holding**   | **lake only**                         | account × instrument × date | Volume, and it is a pure snapshot. `app/lake` already serves it.                                  |
| Performance   | derived                               | —                           | From valuations; not stored (today's `accounts.performance` jsonb becomes a cache, not a source). |

`platform.accounts.value` stays as a denormalised "latest" for listing performance, fed from
`platform.valuations` — one write, two readers, and the listings stop needing a lake read.

**The value rule (Sept 2026).** Every surface that prints an account's or a client's worth prints
`platform.accounts.value` — the account record, the accounts listing, the client portal's home
hero, and the client portal's account detail (`GET /crm/portal/account/{uid}` in
`app/routers/crm.py`). The lake supplies what sits _inside_ the value — holdings, allocation,
cash — and never restates it. The account-detail endpoint used to prefer the sum of the lake's
holdings rows, which is a different number whenever the two snapshots are not the same day's: one
client opened an account worth £2.24M on their home card and £452k on the page it linked to. Its
"as at" date follows the same provenance: `_VALUED_AT` is `data.valued_at` (the legacy ingest's
stamp), else the latest `platform.valuations.as_of_date` for the account (the pipeline's), else
`updated_at` as a last resort — never `updated_at` first, because any unrelated write to the row
made a months-old snapshot read as today's.

### Fund look-through reference data

`lookthrough.{funds,share_classes,snapshots,positions}` is global reference data beside the
canonical book, not another custodian pipeline. IFSL reports describe the constituents of a fund
share class identified by ISIN; they do not describe which tenant owns it. Every dated import is
retained, same-date corrections form a supersession chain, and only the latest correction is
active for that date. Tenancy is applied at read time: `/crm/look-through/{fund_uid}` first proves
that a share-class ISIN occurs in the caller's current RLS-visible holdings. See
[`lookthrough.md`](lookthrough.md).

---

## 3. The conformed lake shape

Every provider's `platform.<entity>` must publish these columns, spelled identically. This is
the contract the materialiser reads; anything provider-specific goes in `data`.

```
external_id        varchar   NOT NULL   -- the provider's own key
key            varchar              -- the slice key this row belongs to (NULL = unresolvable)
lineage           varchar[]            -- root -> leaf ancestry, for subtree filtering
                                       -- NULL = unresolvable, [] = GLOBAL. See below.
source_updated_at  timestamp            -- provider's own last-modified, for change detection
as_of_date         date      NOT NULL    -- the drop/extract this row came from
data               json      NOT NULL   -- the whole provider row, verbatim
<conformed entity fields...>
```

`platform.valuations` does not follow the `external_id` line. A valuation has no provider id of
its own. SEI keys the row on `(account_external_id, as_of_date)` and publishes no `external_id`.
Seccl and Morgan Lloyd still publish `external_id` as that same account id, beside `as_of_date`.
The book stores `account_id`, resolved from the account link.

**`key` + `lineage` is the important one.** It replaces three tenancy dialects with one:

| Provider | `key`                    | `lineage`                                     |
| -------- | ------------------------ | --------------------------------------------- |
| Seccl    | `node_id`                | `node_lineage` (already computed recursively) |
| SEI      | DSR team id (or subfirm) | `[firm_id, subfirm_id, dsr_team_id]`          |
| ML       | `ml_tenant_id`           | `[ml_tenant_id]`                              |

With that, `app/fanout.py::_predicate` collapses to one expression for all providers —
`list_has_any(lineage, [...])` — and `_sei_predicate`'s per-table shape-awareness, the
`_SEI_GLOBAL_TABLES`/`_SEI_NO_SUBFIRM_TABLES`/`_SEI_SCALAR_DSR_TABLES` sets, and the per-provider
config-key map all go away. An entity with no owner (an instrument) publishes `lineage = []` and
is projected whole, which is what `_SEI_GLOBAL_TABLES` hand-codes today.

**`lineage` has two ways of being unset and they mean opposite things.** `[]` is GLOBAL, per the
paragraph above — it projects whole. A row whose owning node could not be RESOLVED is a different
fact and publishes **NULL**, which `predicate.conformed` tests to false so the row reaches nobody.
Twenty-one conformed models spelt the second as the first — fourteen of SEI's seventeen and seven of Seccl's (`coalesce(o.lineage, [])`, or a
`list_filter` over all-NULL inputs), which meant one unresolvable node would have published an
account, its positions, its transactions and its valuations into EVERY enabled tenant's slice —
live for Seccl and Morgan Lloyd, which are filtered by containment today, and dormant for SEI,
which still narrows on the firm/subfirm/DSR triple until step 5 below. Two consequences:

- **A row's org comes from its PARENT, resolved once.** `platform.accounts` is the org of record
  for everything account-grained (`positions`, `valuations`, `transactions`, `portfolios`,
  `cash_schedules`, `upcoming_activities`) and `platform.clients` for the client axis
  (`contact_points`, `relationships`) — they SELECT `key`/`lineage` rather than recomputing from
  the tenancy dialect, so a fact and the account it is about cannot disagree about which node owns
  them. That divergence was real: four SEI models coalesced to their own report's `FIRM_ID` and
  four did not.
- **Only a relation whose rows genuinely belong to no firm may publish `[]`** — the instrument
  register, and the firm-wide fee/transfer/third-party registers described below. The set is named
  in `pkgs/book-sink/tests/test_lineage_sentinel.py`, which scans all three providers' models and
  refuses any other spelling of `[]`, because the failure it guards against is a new model copying
  the wrong idiom rather than a row coming out wrong on today's data.

Per-entity conformed fields (the review's substance — each is the union of what the three
providers publish, named once):

**clients** → `client_type` (`individual`|`entity`), `sub_type` (`trust`|`company`|`charity`,
or NULL — a person has none, and an organisation kind the book does not store stays NULL),
`first_name`, `middle_name`, `surname`,
`entity_name`, `title`, `date_of_birth`, `nationality[]`, `country_of_residence`,
`country_of_domicile`, `national_identifier`, `national_identifier_type`, `email`, `mobile`, `phone`,
`address` (struct: `lines[]`, `city`, `region`, `postcode`, `country`), `status`, `opened_on`,
`base_currency`. Seccl and Morgan Lloyd publish `GBP`. SEI publishes `CLIENT_BASE_CURRENCY`
(blank rows are NULL; the book merge stores GBP). It lands in `crm.contacts.currency`.
Every provider publishes both `mobile` and `phone` (NULL when it has no such number). `mobile`
is the number every custodian can store. `phone` is the other one: Seccl's `phone` object (not
coalesced into the mobile), Morgan Lloyd's `telephone1` (`telephone2` is not read), and a second
SEI contact-details number that is not the mobile and not a fax or a pager. The book merge writes
them to `crm.contacts.mobile` and `crm.contacts.phone`, fill-if-null, so a number the firm has
typed is left alone.

**accounts** → `account_name`, `account_type` (raw), `product_key`, `status` (canonical enum),
`status_raw`, `base_currency`, `opened_on`, `closed_on`, `last_traded_on`, `model_key`,
`fee_package_key`, `owner_external_ids[]` (the clients on it), `is_dormant`, `is_frozen`,
`mifid_classification`, `fees` / `payments` / `withdrawals` (jsonb). The three terms columns
have lived on `platform.accounts` since 0002; Seccl fills them from `derived.account_terms`
(the fan-out's own charge schedule and `recurringPayments`). A provider with no per-account
schedule publishes typed NULLs. Remaining ISA allowance is `platform.accounts.allowance_*`,
filled by the custodian — not summed from the ledger.

**instruments** → `instrument_key`, `name`, `asset_type`, `isin`, `sedol`, `cusip`, `currency`,
`exchange_code`, `market_country`, `issuer_country`, `credit_rating`, `interest_rate`,
`issued_on`, `matures_on`, `ocf`. The ongoing charge is the custodian's figure as published
(SEI's CostsCharges actual, or the estimate when that is the only agreed number; the EMT
extract when the drop has no agreed figure). A blank of both stays null, and a later null does
not clear a figure already stored.

**transactions** → `account_external_id`, `type` (canonical enum), `source_type`,
`source_subtype`, `instrument_key`, `isin`, `asset_name`, `quantity`, `unit_price`,
`gross_amount`, `net_amount`, `fee_amount`, `tax_amount`, `currency`, `transaction_date`,
`trade_date`, `settlement_date`, `posted_at`, `status`, `is_reversal`, `order_key`,
`report_group_kind`, `dealing_charges`, `other_charges`, `fee_type`, `book_cost`,
`realised_gain`, `equalisation_payment`, `subscription_amount`.
Amounts are **signed, `decimal(20,4)`** — the sign convention (credit positive) is applied in
the provider layer, where the `DEBIT_CREDIT_FLAG` / string-double knowledge already lives.
The book keeps those statement facts: `report_group_kind` is the custodian's own statement
bucket (beside canonical `type`, never instead); the dealing/other split sits next to the
total `fee_amount`; pooled `book_cost` / `realised_gain` are the provider's arithmetic, not
a read-time formula; `subscription_amount` is a per-movement drawer fact, not a figure to
sum into ISA headroom. A provider that has no such figure publishes a typed NULL so the
shared book-sink select still binds.

**valuations** → `account_external_id`, `as_of_date`, `market_value`, `cash_value`,
`book_cost`, `unrealised_gain`, `opening_value`, `crystallised_value`,
`uncrystallised_value`, `currency`. SEI aggregates from `platform.positions`; Seccl takes the
account summary; ML from `cam_getscheme`.

The crystallisation split is one fact about the fund, and cash is not. Morgan Lloyd computes
the scheme split and publishes the scheme's own bank balance as `cash_value` (which can be
negative). Seccl states which portfolio group a pension account sits in
(`Crystallised`, `Uncrystallised`, `CrystallisedTransfer`, `Beneficiary`) on the account
detail, and values that side at the account's current value. SEI reads `SIPP_CRYSTALLISED_VALUE` and
`SIPP_UNCRYSTALLISED_VALUE` from the Accounts report onto the latest valuation day, and
leaves both null when the firm has not filled them in. A reader prefers the scheme
administrator's split when that source has stated one, and does not add the custodian's
on top: they describe the same fund. Cash is the opposite — the custodian's uninvested
balance plus the administrator's bank balance, because the custodian does not know about
money that has been passed to the scheme.

**orgs** → `key`, `lineage`, `name`, `type` (`firm`|`subfirm`|`team`|`node`|`tenant`),
`parent_key`, `identifies_firm` (SEI's DSR-type flag), `account_count`.

**fee_packages** → `name`, `category`, `fee_category_raw`, `fee_type`
(`fixed`|`percentage`|`tiered`), `frequency` (`initial`|`ongoing`), `rule` (json — the shape
`platform.fee_packages.rule` stores), `charge_basis`, `payer`, `currency`, `excluded_products[]`.
Three things are settled in the provider layer, because that is where the provider's dialect is
known: `fee_type` is **derived** (Seccl publishes one `tiers` array for all three kinds of
charge, and a structure matching none of them publishes `fee_type` NULL, which the materialiser
counts as a reject rather than guessing a shape that would misprice an invoice); rates are
scaled from the provider's **fraction** to the 0–100 the OLTP CHECK wants; and tier bands are
normalised **contiguous**, because a provider that starts the next band at the next penny
(`[0, 10000]`, `[10000.01, …]`) trips `app/catalogue.py::validate_fee_rule`'s gap check and the
row would arrive un-editable. The **exclusion** is published as stated rather than inverted:
`applies_to` needs the canonical wrapper set and its aliases, which `app/materialise.py` owns.

A fee structure is **firm-wide**, so it publishes an empty `lineage` — the same treatment
instruments and Seccl's transfer-provider register get.

**The root-owned trap, which cost `third_party_providers` its entire register.** Scoping a
firm-wide row to the firm's root org looks tighter and is wrong. `lineage` containment tests
**ancestry**: a row owned by a sub-node publishes that node's root→leaf path, so a tenant holding
any ancestor matches — correct, and why sub-node scoping needs no special case. But the root's own
path is just `[root]`, and a tenant enabled on a _descendant_ holds a key that path does not
contain. `platform.third_party_providers` published the firm root's `[0]` for all 7 of the dev
firm's products, the tenant is enabled on node 1, and the fan-out projected **0 of 7** — which
read as "this firm has no third-party products" rather than as a scoping bug. Both models now
publish `[]` when the owning node is the firm root and keep the node's ancestry otherwise.

The premise underneath the empty path is **one provider firm per catalogue** — `SECCL_FIRM_ID` is
single-valued and a second firm means a second `.env`, metadata schema and control plane, which
`interlace.yaml` spells out for staging vs production. So "every tenant in this catalogue" and
"every tenant of this firm" are the same set. Both models publish `firm_key` so that premise is
checkable rather than assumed, and so it is the column a predicate would start narrowing on if a
catalogue ever held two firms. The general fix — teaching the predicate to expand a tenant's keys
to their ancestors — is the same widening §3 defers below, so it is deliberately not done here.

---

## 4. The OLTP additions

> **Additions since (Sept 2026), all expand-phase.** `0131_staff_profile` — what a firm SAYS
> about its staff (job title, qualifications, FCA reference, bio, photo, direct line) plus
> `provider_refs`, WHICH ID EACH CUSTODIAN KNOWS THE PERSON BY — all **columns on `auth.users`**.
> A `crm.users` table was written first and reverted: the argument for it was that `auth.users` is
> a WorkOS projection the sync overwrites, and the sync does not do that — every
> `UPDATE auth.users SET` in the estate names its columns, and `avatar` is already display data on
> that table. The structural argument (`platform.links.entity_id` is `bigint`, `auth.users.id` is a
> `uuid`, so a login cannot be addressed by the mapping table) is real and is answered by the
> provider's id being a COLUMN here — which is exactly what `workos_user_id` already is for the
> IdP. A custodian's own people are deliberately NOT rows either: `UNIQUE (tenant_id, email)` makes
> it unsatisfiable for exactly the people the mapping exists for (an adviser who is both a Graphene
> login and an SEI user has one email), the read policy gates on a membership so a synced row would
> be invisible to its own firm, and it is 1,591 provider users against a handful of staff. The
> option list is read LIVE from the provider instead — SEI's is
> `GET /employeeservice/firmemployeeusers`. `0132_platform_portfolios` — the container BELOW an
> account, which four SEI write paths require and no `platform.links` row can hold (an account has
> two: 59 of 1,104 in the drop carry a Capital and an Income portfolio); SEI only, because Seccl
> has no sub-account concept and models the same split as a second account. `0120` — the account's
> `bank_accounts` jsonb array (destination for money out, `[0]` the default, client-level as the
> fallback). SEI's sync copies PaidTo onto it (`direction` `pay_to`, `provider_ref` the profile id)
> and leaves entries the firm typed. The wrapper `allowance_*` columns sit beside that array, and
> their reader rule is **`max()` per
> (contact, wrapper), never `sum()`** — a client with two ISAs shares one allowance.
> `0206_allowance_remaining` adds the remaining figure beside used and limit, so a custodian
> that has stated what is left without a derivable annual limit is not stored as if that
> remainder were the limit. `0121` — the
> FATCA self-certification, which SEI enrolment requires and neither other custodian asks for, and
> which is ASKED rather than derived from `tax_residency` (the `tax_relief` rule). `0122` — see
> `docs/data-architecture.md` Decision 1. **`0136_provider_users`** — `auth.merge_provider_user`,
> which makes a custodian's own employee users the **seventeenth merged entity** and the only one
> whose target is not a `platform.*` table. It ATTACHES the provider's id to the login already
> holding the same email (exact, and exactly one — `aml.match_contact`'s bar) and otherwise
> INSERTS one disabled with an `inactive` platform membership: `auth_users_scoped_read` requires a
> membership and does not read its status, while `auth.resolve_claims` grants a portal only on
> `status = 'active'`, so the row is readable by its own firm and grants nothing. It reverses
> 0118's "a custodian's own people are NOT rows in this table" — that migration's header records
> the three objections and how each is answered, and is worth reading before loosening any of it.
> **`book_writer` still holds no privilege on `auth.users`**: the function is SECURITY DEFINER
> owned by `service_role`, so the pipeline can execute those statements and nothing else.
> **`0124_user_nature`** + **`0137_folded_email_and_adviser`** — the email is **stored folded**
> (`provisioning.fold_email`, enforced by `users_email_is_folded`), so `users_tenant_email_key`
> from 0001 gives case-insensitive uniqueness on its own and 0124's expression index is gone. That
> removed an ambiguity 0123 had to refuse rather than a symptom it had to tolerate, and fixed an
> invite path that minted a second login for a differently-cased address. Plus
> **`auth.users.type text[]`** (`client` and/or `staff`) and **`is_adviser boolean`**: what a
> login IS, as against what `auth.organisation_memberships` says it may DO. Never an authorisation
> input — no RLS policy and not `auth.resolve_claims` may read them, pinned by
> `tests/test_user_nature.py`, because the identity model deleted a `role='client'` belt of exactly
> that shape. `is_adviser` is separate because advising is a capability WITHIN staff
> (`users_adviser_is_staff`), and no custodian can propose it: SEI's `DSR_TYPE_NAME` has seven
> 'Adviser' codes and zero accounts use one, Seccl's `userType` is `Machine | User`, Morgan Lloyd
> names one adviser contact per firm. It gates `contacts.assign_adviser`, which previously accepted
> any login in the firm as an adviser — a client's own login included.

Expand-phase only — new tables and new nullable columns, no rewrites of `0002`.

```sql
-- Provider org units: the thing a tenant's slice is actually keyed on.
CREATE TABLE platform.nodes (
  id            int PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  tenant_id     int  NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  provider      text NOT NULL,
  key       text NOT NULL,
  lineage      text[] NOT NULL DEFAULT '{}',
  type      platform.type NOT NULL,
  name          text,
  parent_key    text,
  data          jsonb,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT orgs_unique UNIQUE (provider, key)
);

-- Securities. Global reference, NOT tenant-scoped: an instrument is not owned.
CREATE TABLE platform.instruments (
  id            int PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  isin          text,
  sedol         text,
  cusip         text,
  name          text NOT NULL,
  asset_type    text,
  currency      text,
  exchange_code text,
  data          jsonb,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT instruments_isin_unique UNIQUE (isin)
);

-- Account valuation history: one row per account per date.
CREATE TABLE platform.valuations (
  id              bigint PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  tenant_id       int  NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  account_id      int  NOT NULL REFERENCES platform.accounts(id) ON DELETE CASCADE,
  as_of_date      date NOT NULL,
  market_value    numeric(20,4) NOT NULL,
  cash_value      numeric(20,4),
  book_cost       numeric(20,4),
  unrealised_gain numeric(20,4),
  opening_value   numeric(20,4),
  -- 0117. The scheme administrator's own computation; NULL for a custodian, never 0 (which would
  -- state the fund is wholly uncrystallised).
  crystallised_value   numeric(20,4),
  uncrystallised_value numeric(20,4),
  currency        text NOT NULL DEFAULT 'GBP',
  source_system   text NOT NULL,
  data            jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT valuations_unique UNIQUE (account_id, as_of_date, source_system)
);

-- Models exist on more than one platform (data-arch Decision 1 named this join).
CREATE TABLE platform.model_platforms (
  id          int PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  tenant_id   int NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  model_id    int NOT NULL REFERENCES platform.models(id) ON DELETE CASCADE,
  platform_id int NOT NULL REFERENCES platform.platforms(id),
  external_id text,
  CONSTRAINT model_platforms_unique UNIQUE (model_id, platform_id)
);
```

Plus: `platform.accounts.org_id`, `crm.contacts.org_id` (both `int NULL REFERENCES
platform.nodes`), and `platform.transactions.instrument_id int NULL REFERENCES
platform.instruments` — the canonical replacement for today's bare `isin` text.

Each new tenant-scoped table ships its RLS policy in the same migration, per the repo rule.
`platform.instruments` is the one exception and is deliberately global: `GRANT SELECT` to
`authenticated` with no policy, because an ISIN is not tenant data.

### Where the provider payload lives

`platform.links.data` — **not** the entity's `data`. An entity can hold links to two
providers (the same client at SEI and at Seccl); one `data` column on the entity cannot hold two
payloads without inventing a provider-keyed object inside it, which is a link table with extra
steps. The rule:

- `platform_links.data` — the provider row, verbatim, as the conformed model published it.
- `<entity>.data` — canonical extras that belong to no provider (staff-entered, derived).

This also fixes an overlap `0002` left: `platform.accounts.platform_identifier`,
`products.platform_identifier` and `models.platform_identifier` all predate `platform_links` and
now say the same thing worse (one provider only, no `link_type`). They become deprecated in
favour of links, contract-phase.

---

## 5. The sync — and the answer on Python sinks

**This section's original recommendation — a sink for _conforming_ only, with the OLTP write staying
in the API — was reversed in August 2026. `docs/provider-pipeline.md` is the landed design; the four
objections below and what became of each are kept because three of them still hold.**

```
provider daemon (interlace DAG)                                    │  API
  raw.* ──▶ platform.* (conformed, §3) ──▶ tenant_{hex}.{entity}   │  reads the book
                                            │                      │  (no longer the writer)
                                            ▼                      │
                                     inbound_{provider}.{uid}__{entity} │
                                            │  (Postgres, book_writer)
                                            ▼
                                     platform.merge_entity / merge_linked_entity
                                            ▼
                                     crm.contacts, platform.{orgs, accounts, instruments,
                                     transactions, valuations} + links
```

| The objection                                                                                | What happened                                                                                                                                                                                                                           |
| -------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`service_role` credentials** — three daemons with write access to the portals database     | **Answered, not accepted.** They connect as `book_writer`, which reaches the merged relations and nothing else (0062). `BYPASSRLS` is unavoidable for a cross-tenant writer and is stated plainly; grant scope is the actual control.   |
| **Tenant identity lives in `platform.tenant_platforms`, RLS-protected and the API's domain** | **Reversed.** `book_writer` holds SELECT on it and on `auth.tenants`/`storage.tenants`. An internal endpoint would have been a second copy of the registry with its own refresh cadence and its own failure mode.                       |
| **Links are audited**                                                                        | **Still true, and unchanged in substance.** The pipeline mints them with a NULL actor — which `app/materialise.py` already did, since it has no principal either. What makes that safe is that the merge does not write unchanged rows. |
| **One writer**                                                                               | **Still true, and now enforced in one place.** The `ON CONFLICT` contract is inside the two SQL functions rather than restated per call site, so "one writer" became "one policy".                                                      |

What did NOT move is `SECRETS_MASTER_KEY`, which was the real objection: the pipeline needs no tenant
object-store credential, because the leg that needs one — the tenant's own parquet — is still the
API's. See `docs/provider-pipeline.md` §6.

**The conform step was always DAG work** and that half of the recommendation stands: it depends on
`raw.*`, it belongs in Lineage, and it wants interlace's retries and run history. The conformed models
are plain SQL, so most of it needed no Python at all — and the **ledger** the section wanted (row
counts and rejects per entity per run) is now two things: `platform.merge_rejects` in the book, and
`book_{uid}.merge_ledger` in the warehouse.

**The materialiser** (`app/materialise.py`) generalises `scripts/ingest_seccl.py`, which already
does this shape for Seccl: read the analytical source, upsert by external id via
`platform_links`, run as `service_role`, idempotent. Per (tenant, provider, entity):

```
for row in lake.query(f"select * from platform.{entity}"):        # tenant catalogue, read-only
    entity_id = link_lookup(provider, entity_type, row.external_id)
    if entity_id is None:
        entity_id = insert_canonical(row)                          # conformed fields only
        insert_link(provider, entity_type, entity_id, row.external_id, data=row.data)
    elif row.source_updated_at > link.updated_at:
        update_canonical(entity_id, row); update_link(data=row.data)
```

Order matters and is fixed: **orgs → instruments → models → products → contacts → accounts →
valuations → transactions**, because each resolves foreign keys against the previous. A missing
parent is a recorded reject, not a silent NULL.

Products sit before accounts for the same reason: the shelf is discovered from the accounts the
firm holds, and its `product_key → id` map is how each account resolves the wrapper it is held
in. That map was being computed and discarded, so `platform.accounts.product_id` was never
written — and an account with no product has no `product_type`, which is what every "is this a
pension?" surface gates on, the drawdown journey included. Discovery needs
`platform.platforms` to exist (`product_platforms` FKs it); it is seeded from
`providers.PROVIDERS` by `sync_platforms()` on startup, not by a migration.

**Trigger.** No trigger any more — that is the point of the reversal. The merge is a node in the
provider's DAG (`book_{uid}.merge_ledger`), so it runs because its staging models ran, in the same
run, with interlace's retries and run history. `fanout_listener.py`'s event-log poll was exactly the
coupling a DAG exists to remove: it also meant a manual `POST /admin/fanout/run` projected the lake
and never materialised, so a green fan-out left the book empty.

**Idempotency, three layers** (unchanged from Decision 4): raw object key; the conformed row's
`(external_id, as_of_date)`; the OLTP `platform.links` unique constraint — which as of `0066`
includes `tenant_id`, because a SHARED entity (`platform.instruments`) is legitimately mapped by
every firm that holds it, and the old key let only the first one record that it did.

---

## 6. Sequencing

1. ~~**Conform one provider**~~ — **done.** `services/seccl/models/platform/*` publish the §3
   shape with a declared `key` (they were keyless views); `orgs` supersedes `nodes`, and
   `accounts`, `instruments` and `valuations` are new — Seccl had no accounts model at all.
   `tests/test_platform_models.py` stubs the raw tables in DuckDB and executes each model, so a
   binder error or a mistyped column fails locally rather than in a deploy.
2. ~~**Migration**~~ — **done.** `0031_platform_conformed`: `platform.nodes`,
   `platform.instruments`, `platform.valuations`, `platform.model_platforms`, the FK columns,
   RLS + audit. Expand only, applied and verified against local Postgres.
3. ~~**Materialiser**~~ — **superseded and DELETED** (August 2026). `app/materialise.py`,
   `app/fanout.py`, `app/fanout_listener.py` and `POST /admin/{fanout,materialise}/run` are gone;
   the pipeline that replaces them (`docs/provider-pipeline.md`) runs all three custodians and is
   verified against a real book. `provider_schema` moved to `app/providers.py`, the product
   vocabularies were already duplicated in `book_sink.entities` (verified identical before the
   delete), and `_ORG_TYPES` is superseded by each provider publishing a canonical `type`.
4. ~~**Conform SEI**, then **ML**~~ — **done.** Both publish the §3 shape and merge into the book.
5. **Collapse the SEI predicate** to `lineage` containment once its enablement config is migrated
   to leaf org keys. Not done, and not merely pending: containment tests ancestry, so
   firm-AND-subfirm-AND-team becomes OR and every multi-axis slice widens. `predicate.sei` keeps its
   shape-awareness until then — with `orgs` already on containment, because the conformed org
   dimension carries `lineage` and no `firm_id` at all.
6. **Valuations backfill.** `platform.accounts.value`, the listings' `cash`/`growth`, the
   account record's cash and a cash withdrawal now read the book: value from the latest
   valuation's market value, cash from that row's custodian `cash_value`. A model withdrawal
   still reads invested holdings from positions.
7. **Contract phase** — drop the `platform_identifier` columns.

### What step 3 proved in the end

The open question was whether a real drop's row counts survive the round trip; they do, for all
three custodians, and the evidence is in `docs/provider-pipeline.md` §6. Seccl matches every count
the old path's capture holds; SEI merges 62 orgs / 6,170 instruments / 411 clients / 505 accounts /
467 valuations with zero rejects; ML's clients merge as `created 0` because it recognised the links
`sync-to-morgan-lloyd` had already minted. A forced re-run writes nothing on any of them, which is
the idempotence `platform.links`' audit trigger depends on.

**Cash from step 6 is the valuation, not the positions file.** A cash withdrawal and the
account record read the custodian's latest `cash_value`. Positions keep only the latest file,
and that file can omit an account the previous file still valued, which is how the record
showed £0 against a listing that showed the balance. A model withdrawal still reads invested
holdings from the firm's positions slice. `platform.positions` stays lake-only: the holdings
table is a list of positions, and the cash figure is not.

## 7. Open questions

- **Holdings in OLTP?** Proposed no (lake only, §2). If drawdown/CGT work needs holdings inside a
  transaction with OLTP writes, that changes.
- **Instrument identity without an ISIN.** Cash positions and some SEI instruments have none;
  `UNIQUE (isin)` cannot be the only key. Proposed: `UNIQUE (isin)` where present, plus links per
  provider for the rest — an instrument is then reachable by provider id even when unidentified.
- **Canonical status vocabularies.** `platform.account_status` exists; SEI's numeric codes need a
  mapping table (`ref.codes` has the labels). Proposed: map in the provider layer, keep
  `status_raw` alongside, so a new code degrades to "unmapped" rather than breaking the sync.
- **ML is not a custodian.** It is scheme administration, so it has no accounts in the custodian
  sense, and forcing it into the account shape would invent data. Its canonical contribution is
  clients, transfers and benefit applications.

  **It DOES publish valuations now (Sept 2026), and not in the custodian sense either.** We PUSH
  it the valuation (`cam_createpolicyvaluation`), so the fund total `cam_getscheme` answers with
  is our own number re-derived — the two things it adds are the CRYSTALLISATION SPLIT it computes
  and the SCHEME'S OWN BANK BALANCE, cash the client holds at a second place and could not see
  anywhere. Hence the standing rule for every reader of this table: **take a VALUE from one
  source system, take CASH from all of them.** Summing `market_value` across sources
  double-counts the investments; summing `cash_value` is the point. Recorded in 0117 and pinned
  by `apps/api/tests/test_valuation_sources.py`.

  Safe against inventing accounts as a property of the entity rather than a promise: `valuations`
  resolves `account_id` via `link:account` with `required=True`, so a Dynamics scheme nobody
  created through `sync_client` becomes a counted `platform.merge_rejects` row. (It publishes them under
  the §3 names — `platform.clients`, not `platform.contacts`. The materialiser asks every
  provider for the same relation names, so publishing the right columns under a provider's own
  vocabulary is as unreadable as publishing the wrong columns: while ML's was `contacts`, every
  ML client was a counted `unavailable` reject and none reached `crm.contacts`.)

---

## 8. The firm-configured catalogue (`0032_platform_catalogue`)

§2-§5 cover what the custodians publish. This is the other half: what a firm configures, which
the admin app edits and the portals read. All of it is tenant-scoped with forced RLS.

**Products are an entity, not a platform's list.** `0002` gave `platform.products` no
`tenant_id` and a `USING (true)` read policy, so a product was global — "the Graphene SIPP" was
indistinguishable from any other firm's SIPP. Products are now tenant-scoped, and
`platform.product_platforms` carries one row per platform with that platform's own reference
and name (`external_id`, `external_name`, `external_type`): a Seccl `clientProductId` and an
SEI product code describe the same shelf item under different names, and the firm's name for it
is neither of theirs. The three pre-existing rows keep `tenant_id IS NULL`, which reads as
"unclaimed catalogue entry" — inventing an owner would be worse than leaving them visible and
read-only.

**Kickoff rules live on the product.** `allowed_custodies` states whether the shelf item can be
onshore, offshore or either; `max_parties` caps the account-party memberships created at journey
kickoff (existing rows default to two); and `allowed_client_types` states whether an individual,
an organisation or either may own it. Statutory person-only wrappers (ISA and JISA) cannot be
widened to organisations. `allowed_currencies` narrows the firm's currency policy for the product;
it is non-empty, cannot contain a currency the firm does not allow, and ISA/JISA are GBP-only.
`bank_details_allowed` states whether an account may hold a withdrawal
destination; existing JISAs are disabled because they cannot be withdrawn from before conversion
to an ISA. The provider choice is the intersection of the product's
enabled `product_platforms(role='custodian')` mappings and the firm's enabled
`tenant_platforms`. Enabling SEI writes those custodian rows for GIA, ISA, JISA
and SIPP (`providers.attach_custodians`); a mapping with a null role does not
count, so a template save that omits `role` stores the provider's declared role
when the row has none. Custody is the same intersection against what that custodian can open:
Seccl is onshore only; SEI follows `tenant_platforms.config.custody_orgs` (a firm with no
subfirms keeps both Graphene locations and sends no account org). A sole remaining
custodian or custody is derived; several become an account-level choice, defaulting to
onshore. These are product facts, not flow intake settings: every flow opening that
product must honour the same operational limits.

**Models carry what the providers publish, plus history.** Seccl sends `aim`, `modelType`,
`riskRating`, `investmentDecision`, `modelCharge`, `vatable`, `suspendRebalances`,
`cashAllocation` and `productType`; each is a column. **`modelCharge` is a fraction
(`0.002` = 0.2%)** and is scaled ×100 at the conformed layer, the same trap as fee rates and
allocation weights — `platform.models.model_charge` is 0–100, matching `target_return` and
CTC's `% management charge`. **Target return is first-class**
(`target_return`, `target_return_basis`, `target_return_horizon_years`) because firms have been
putting it in Seccl's `url` field for want of anywhere else — and 4% alone is not a target, so
the basis (absolute, above inflation, above a benchmark) is stored with it.

`platform.model_versions` + `platform.model_allocations` record allocation change as versioned
snapshots rather than per-line edits: a rebalance is one event, the previous version stays
queryable, and a version is a draft until `published_at` is set. `platform.accounts.model_id`
keeps pointing at the model, not the version — an account tracks the model as it evolves.

`platform.model_commentary` stores **markdown only**, optionally against a version (explaining
that change) or standing against the model. No HTML: rendering is the reader's job, and stored
HTML is an injection surface every template would have to sanitise on each read.

**Fee packages** keep the `fixed | percentage | tiered` rule shape the reference implementation
settled on (`~/dev/graphene-platform`), which `0002` already matched. What Seccl and SEI add on
top of it becomes columns rather than more jsonb, because these are filtered on: an effective
window (`effective_from`/`effective_to`, Seccl's tiered adviser charges carry one),
`minimum_amount`/`maximum_amount`, `applies_to` instrument types, `charge_basis`, `payer` and
`vatable`. `platform.fee_package_platforms` holds each platform's own fee reference.

The register is **synced, not only authored** (`GET /v1/fee-structures/{firmId}` →
`platform.fee_packages` in the lake → `app/materialise.py::_sync_fee_packages`), which needed
`0057`: `0002` gave `fee_packages` an `external_id` and no constraint on it, so a nightly run
would have inserted the firm's charges again every night. It is keyed `(tenant_id, external_id)`
and **not** the name — `0046` learned that on `transfer_providers`, where two genuinely different
rows share one name and the second upsert erased the first. A discovered structure arrives
**enabled**, unlike the provider registers: those are the platform's whole address book and a
firm uses a handful, whereas a fee structure exists because someone at the firm priced it.
`minimum_amount`, `maximum_amount`, the effective window and `vatable` are **not** populated by
the Seccl sync — that endpoint's documented payload is `{id, type, name, tiers,
excludedWrapperTypes}` and nothing more, and binding those columns to guessed field names would
publish NULL while looking checked. They stay for the admin catalogue to set.

**Two provider registries, deliberately not one table with a `kind`:**
`platform.third_party_providers` is whose products a firm can hold alongside its own;
`platform.transfer_providers` is who a transfer cedes from or to, and it carries the ceding
references (`masterparty_id`, `origo_id`) a third-party product has no use for. The same
organisation appears on both lists with different products enabled, which a single table would
force into one row.

### Terminology (`l10n`)

Personalisation, not translation: a wealth manager says "instruments" where a platform says
"assets", and using the wrong word on every page is a papercut. `l10n.terms` is the global,
seeded catalogue of ~17 overridable nouns (key, default label, plural, description); a tenant's
`l10n.overrides` row replaces the label. The catalogue is what decides which keys exist, so the
admin UI cannot invent a term no portal reads. Its own schema because it is not platform data,
and one language throughout — there is no locale column, no message catalogue and no plural
rules beyond a second field.

### One mapping surface: `platform.entity_links`

`platform_links` maps entities that have an identity of their own. Transactions and valuations
deliberately have no link rows — their provider key is a column on the fact itself
(`source_system` + `source_transaction_id`; `(account, date, source)` for a valuation), which
_is_ the mapping, denormalised. A link table beside a multi-million-row fact doubles the write
for no new information. The `platform.entity_links` view unions the three so a reader asks one
question — `entity_type`, `entity_id`, `provider`, `external_id` — without the admin app
needing to know which mechanism recorded it.

### A migration lesson worth keeping

The `model_platforms` backfill in this migration silently copied **zero rows** on its first run:
the migrator role is subject to FORCE ROW LEVEL SECURITY and is not `bypassrls`, so with no
request claims its `SELECT` saw nothing. Any DML in a migration must run
`SET LOCAL ROLE service_role` first — `0023_flows_hardening` had already learned this and says
so; this one had to learn it twice.
