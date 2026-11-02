# Morgan Lloyd Integration — Reference Documentation

Morgan Lloyd (ML) is a SIPP/pension provider being integrated into Graphene. This folder is the living reference for that integration: workflows, API surface, data mapping, state machines, and operations.

> **Imported from `graphene-platform/docs/morgan-lloyd` (14 Jul 2026).** In _this_
> repo the implementation lives in `pkgs/morgan-lloyd` (typed CRM client + the
> option-set enums, action names verified against the live vendor docs) and
> `services/morgan-lloyd` (interlace sync + `populate`/`illustrations` scripts) —
> where this folder's operation catalogue and the live docs disagree on an action
> name, the client's endpoint mixins are authoritative. System-landscape sections
> below that reference `services/api`, Next.js apps or PGMQ describe the
> graphene-platform repo, not this one.

> **The field-level contract is not in this folder — it is generated.**
> `pkgs/morgan-lloyd/src/morgan_lloyd/vendor_manifest.json` is a machine transcription of
> <https://dev.api.morgan-lloyd.co.uk/>: every documented operation with its fields, types,
> requiredness and option sets (65 operations carry an action; 63 carry a field table). Produced
> and re-checked by `moon run morgan-lloyd:{build-manifest,check-manifest}`, read through
> `morgan_lloyd.manifest`.
>
> It lives inside the package rather than here because the API reads it at run time — to choose
> the columns the admin entity tables show and to build the action forms — and a wheel does not
> ship this directory. It is generated rather than written because a field name a human (or a
> model) mistypes becomes a payload a live Dynamics org rejects, or worse, accepts wrongly.
> `enums.py` remains the hand-transcribed option-set vocabulary; `tests/test_manifest.py`
> reconciles the two rather than letting a second copy drift.
>
> **The introducer is not stable, and naming only two phrasings cost eleven option sets.** The
> page writes "Allowed Values", "Accepted Values", "Possible Values", a bare "Values:" and once
> "Possible Values: Values (", so the parser anchors on the word `values` immediately before the
> open paren rather than on a list of the phrasings seen so far. The eleven missed were
> `cam_createupdateliability.cam_liabilitytype`, `cam_canceldrawdown.withdrawalType` and **ten on
> `cam_drawdownsetup`** — `drawdownOption` (30 members, four of whose labels contain their own
> comma), `incomeFrequency`, `investmentPathway`, `paymentDay` and the six guidance/advice
> attendance flags. A field with no parsed options renders as a FREE TEXT BOX for a closed set and
> `platform_actions.validate` can only check a set it has, so the whole drawdown instruction — the
> one console action that moves a client's money — was typed by hand and unvalidated on both
> sides. `multiselect` is marked from the page's own wording for the same reason it cannot be
> inferred: the vendor types those fields `string`, indistinguishable from a free-text name.

> Source material (vendor-supplied) lives alongside these docs:
> `Morgan Lloyd API Details- Graphene.docx`, `Morgan Lloyd API diagram.pdf`, `XML_v1.6_Bespoke.pdf`,
> `Scheme_Illustration EXAMPLE.pdf`, and the illustration/valuation/letter PDFs and report
> spreadsheets in this folder.
>
> **The workflow spreadsheets are in [`workflows/`](./workflows/) — thirteen of them**, not the
> three named here before: account open, contribution, transfer in, in-specie transfer, transfer
> out, withdrawal, fees, illustration, death, ill health, serious ill health, client
> communications, and the touch-points workbook. Every one is read and reconciled in
> `workflows-reconciled.html`.

## Index

| Doc                                                      | Contents                                                                                       |
| -------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| [workflows-reconciled.html](./workflows-reconciled.html) | **All 13 workflow spreadsheets** against the ML operations, SEI endpoints/codes, and the build |
| [illustration-fields.html](./illustration-fields.html)   | **Every CTC1 element**: spec options, what we send, which fields come from the client profile  |
| [workflows.md](./workflows.md)                           | The six end-to-end workflows (A–F) with sequence narratives                                    |
| [api-reference.md](./api-reference.md)                   | ML Dynamics/OData API: auth, `cam_*` operation catalogue, fixed reference values               |
| [endpoint-coverage.md](./endpoint-coverage.md)           | The client audited against that catalogue: naming divergences, `$metadata`, the merged spec    |
| [data-mapping.md](./data-mapping.md)                     | Graphene data model → ML field mapping + CTC XML v1.6 element map                              |
| [state-machines.md](./state-machines.md)                 | Application, contribution, transfer, drawdown, valuation, recon states                         |
| [illustrations-report.md](./illustrations-report.md)     | What CTC actually calculates, measured against real PDFs                                       |
| [ctc-xml-schema.md](./ctc-xml-schema.md)                 | The CTC1 Bespoke XML v1.6 element map                                                          |
| [operations.md](./operations.md)                         | Scheduler, SFTP ingest, manual-intervention queues, recon, secrets                             |

**Start with `workflows-reconciled.html` when the question is "does this work end to end?"** It is
the only doc that reads the vendor spreadsheets, both providers' operations and the code together —
and it records where the other docs here have drifted from the build (`state-machines.md`'s transfer
saga and `reconciliation_breaks` table, `api-reference.md`'s operation names, `workflows.md`'s six
against the folder's thirteen). Where it disagrees with a neighbour, it was checked against the code
or a live response; the neighbour usually was not.

## The three external systems

| System                | What it is                                                      | Role                                                                               | Integration                                                           |
| --------------------- | --------------------------------------------------------------- | ---------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| **Morgan Lloyd (ML)** | Dynamics 365 / Dataverse back office (Clifton Asset Management) | The SIPP **scheme** administrator                                                  | OData v4 REST, Azure AD S2S OAuth2 (`cam_*` operations)               |
| **SEI / SWP**         | SEI Wealth Platform                                             | **Custody** & investments (holds the money)                                        | Already integrated — `services/api/app/platforms/sei/` (mTLS + OAuth) |
| **CTC**               | Calculation engine + **DCube** hosted journey                   | **Illustrations** (XML→PDF) and the **benefits/drawdown** client journey (via SSO) | Bespoke XML v1.6; DCube reached by SSO link from ML                   |

Graphene is the **system of record for onboarding and the book of business**, and the **orchestrator** that keeps Graphene ↔ SEI ↔ ML consistent. Reconciliation is three-way: Graphene book / SEI custody / ML scheme.

## System landscape

```
 Client / Adviser
        │
        ▼
 Graphene UI ─ apps/platform (Next.js, :3001)   admin/ops + client onboarding (/a/*)
        │ REST
        ▼
 services/api (Python FastAPI, :3002)            platform-adapter registry + PGMQ sync worker
   ├── platforms/sei/          ✅ implemented
   ├── platforms/morgan_lloyd/ ⛔ to build (stub today)
   ├── sync/  (PGMQ: sync_jobs, links)  ✅
   └── workers/sync_worker.py  (submit→poll→complete/retry) ✅
        │
   ┌────┼───────────────────────────┐
   ▼    ▼                            ▼
 SEI   Morgan Lloyd (Dynamics)     CTC (XML→PDF) + DCube (SSO)
        │
 sei.duckdb / dataflow (Prefect + DuckDB) — daily analytics / AUM source
```

Shared Postgres schemas: `platform`, `onboarding`, `events`, `crm` — owned by Drizzle in `pkgs/db`; `services/api` reads/writes via raw asyncpg SQL.

## Status at a glance

- **Reusable sync engine** (adapter registry, `SyncWorker`, PGMQ, `platform.sync_jobs`/`platform.links`) exists and is provider-agnostic.
- **ML adapter is a stub**; **ML config keys, SFTP, scheduler, and the valuation/contribution/transfer/beneficiary/drawdown tables do not exist yet**.
- See the implementation plan for the phased roadmap (Phase 1 = Daily Valuations).

## Data acquisition — OData entity sets, not just custom actions (Aug 2026)

`pkgs/morgan-lloyd` implements 51 of the org's 87 `cam_*` **actions**, and those are how you
WRITE to Morgan Lloyd. A handful of them read, but only one scheme or one contact at a time —
which is why `models/raw_scoped.py` needs `ML_SYNC_SCHEME_REFS` / `ML_SYNC_ADVISER_EMAILS`,
and why `raw.{schemes, contribution_applications, received_contributions, bank_transactions}`
had been landing **empty tables on every run**: neither variable was set. The premise recorded
in that module — that the CRM cannot enumerate schemes for us — is not true. `cam_schemerefs`
lists them.

Dataverse also exposes every table as a plain OData collection, and those enumerate
themselves. `models/raw_odata.py` lands 17 of them whole (`full_merge`, paginated by
`get_collection`).

### What is actually reachable

Measured against `cliftonassetdev` by probing all 157 `cam_*` entity sets plus the standard
tables — **38 return rows, 39 are empty, 85 return 403**:

|                            | rows  |
| -------------------------- | ----- |
| `cam_pensioncontributions` | 1,552 |
| `cam_providers`            | 1,336 |
| `cam_enquiries`            | 1,226 |
| `cam_benefitsapplications` | 925   |
| `accounts`                 | 488   |
| `cam_fees`                 | 48    |
| `cam_schemerefs`           | 7     |

**The 85 forbidden tables are out of scope**, not a permissions gap to chase: none of them appears
in Morgan Lloyd's published API documentation (dev.api.morgan-lloyd.co.uk), so they were never part
of the contract. `cam_assetvaluations`, `cam_policyvaluations`, `cam_cashbalances`,
`cam_attitudetorisks`, `cam_bcetranches` and `cam_annualreviews` are among them; if valuation or
risk data is needed later it is a conversation about extending the API, not about our role.

Audit, email, task, annotation and checklist tables are readable and deliberately **not**
synced: no consumer, and personal data we have no reason to copy.

### The conformed platform layer

`platform.{clients, transfers, beneficiaries}` were bare `SELECT *` views with no declared
key. They now publish the docs/platform-model.md §3 spine (`external_id`, `key`,
`lineage`, `source_updated_at`, `as_of_date`, `data`), which is what lets the materialiser
read Morgan Lloyd the same way it reads Seccl. ML's org axis is a single Microsoft tenant —
one Dynamics/Azure tenant per firm — so `lineage` is a one-element path; there is no
hierarchy to walk.

**The person relation is `clients`, not `contacts`.** §3 names it `clients` and Seccl and SEI
both publish under that name; ML's was `platform.contacts` until August 2026. The columns were
already right, which is what made it hard to see: `app/materialise.py` asks every provider for
`clients`, so every ML contact came back as a counted `unavailable` reject — indistinguishable
in the ledger from "this tenant has no ML slice" — and `crm.contacts` held no morgan_lloyd row
at all. Two names had to agree and one of them was ours: the fan-out's `_PROVIDER_TABLES` and
the model file. The provider a relation came from is carried by the catalogue (`ml.*`), never
by the relation name.

Field names are verified against the live entities rather than taken from the docs: `contacts`
carries 457 columns and `cam_personalpolicies` 536, and only confirmed ones are surfaced — the
rest stay in `data`. A transfer VALUE is not among them; the amount lives on the linked
financial transaction, a join this layer does not yet make.

### `$metadata` is the source of truth

`GET /api/data/v9.1/$metadata` with `Accept: application/xml` (31 MB) is the complete,
versioned list of actions, entity sets and parameter names. Both `api-reference.md` (47
actions) and the client (51) undercount the org's 87. See `endpoint-coverage.md` §6.

## Syncing a client into Morgan Lloyd

Enabling Morgan Lloyd for a firm (`PUT /admin/tenants/{uid}/providers/morgan-lloyd`,
`providers.set_provider`) maps every **SIPP** on that firm's shelf to Morgan Lloyd as
`role='scheme_administrator'`. A SIPP created afterwards gets the same row. A third-party
pension (`TPPENSION`) is not mapped — it is administered elsewhere; `is_pension` is the
display flag for the client record, not this one. Existing per-product `ml_scheme_type` /
`ml_sipp_type` pairings are preserved; a first attach copies the tenant config's
`scheme_type` / `sipp_type` so `cam_createscheme` has a pairing without a second admin
visit. A GIA or ISA is not mapped — Morgan Lloyd does not administer those.

Onboarding `setup_account` then creates or resumes the client, scheme and policy
(`app/account_enrol.py` → `sync_client`) once the Graphene draft is active. A vendor refusal
(missing NI number, half-configured scheme types, credentials) is stamped on
`platform.accounts.data.platform_sync` and does **not** fail the journey; the account record's
**Sync to Morgan Lloyd** button is the retry. Custodian enrolment (Seccl / SEI) stays
`custodian_open.status = pending_ops` — those write-backs are not built.

`POST /crm/contacts/{id}/sync-to-morgan-lloyd` and `POST /crm/accounts/{uid}/sync-to-morgan-lloyd`
(`app/integrations/morgan_lloyd/sync.py`) create whatever a client — or one of their SIPPs — is
missing and record each identifier. Five steps, in the vendor's order:

1. the **adviser** — `cam_CreateFinancialAdviserContact` → a Dynamics GUID on
   `auth.users.provider_refs['morgan-lloyd']` (the client's adviser; the deployment's
   `adviser_contact_id` is the fallback when nobody is assigned);
2. the **contact** — `cam_createcontact` → a Dynamics GUID on `platform.links`;
3. a **scheme per pension account** — `cam_createscheme` → a reference (`MLSIPP/00001`) recorded
   the same way against the account. Non-pension accounts are skipped: a GIA or ISA has no scheme
   in Morgan Lloyd's model;
4. a **policy / investment** per pension account — `cam_createwrapper` → the account's uid as
   the policy number, recorded as `platform.links(link_type='policy')`;
5. **beneficiaries** on that scheme — `cam_createbeneficiaries` → a nomination GUID on
   `platform.links` (`entity_type='beneficiary'`). The member's address is the fallback when the
   nomination has none.

**It is idempotent and resumable.** Every step checks its recorded identifier first and reports
`skipped`. That is not a nicety — the previous single-step endpoint created a contact, failed to
read the id out of the action envelope, and recorded nothing, so each retry duplicated the person
in a real back-office system. A per-account failure is reported rather than raised, so a partial
run says which half worked instead of hiding it.

### The adviser firm is a prerequisite

`MORGAN_LLOYD_ADVISER_FIRM_ID` is the Dynamics `accounts` GUID of the adviser firm the connection's
clients belong to, sent as `cam_financialadviser_Account`. It must be an **entity reference** —
`/accounts(<guid>)`; a bare, braced or uppercased GUID all fail with "Invalid Format". A contact
created without it is unattached, and `cam_createscheme` against it answers
`403 0x8004f502 … does not have create permissions to a ML Team secured field`. The sync warns up
front when it is unset rather than letting that surface later as a permissions error.

One per connection, because the model is one Dynamics/Azure tenant per firm — the same assumption
`MORGAN_LLOYD_TENANT_ID` already encodes. A firm running several adviser entities inside one
Dynamics org would need this on `platform.tenant_platforms.config` instead.

`MORGAN_LLOYD_SCHEME_TYPE` and `MORGAN_LLOYD_SIPP_TYPE` are required to create a scheme. Both are
firm-specific option-set values that Morgan Lloyd documents without labels, so they are
configuration: unset, scheme creation refuses with a message naming them rather than sending a
plausible-looking wrong value into a live system.

**They are the FALLBACK now, not the source (Sept 2026).** ML's SIPP covers scheme types 16 **and**
17, so which pair an account needs is a property of the PRODUCT it was opened under, not of the
deployment — a firm offering two SIPP products cannot express that in one pair of environment
variables, and one deployment serves many firms. `sync.scheme_types` reads
`platform.product_platforms.data` (`ml_scheme_type` / `ml_sipp_type`, an allowlist validated by
`catalogue.PRODUCT_PLATFORM_SETTINGS` — the column existed from 0042 and nothing could write it)
and falls back to the settings, which is the right answer for the firm with one SIPP.

Two distinctions that both refuse rather than complete a pairing nobody chose: a **half**-configured
product (one of the two set) is refused rather than topped up from the deployment, and a value that
is present but **unreadable** is refused rather than treated as absent — 14 pairings are valid, so
the wrong valid one simply succeeds and the vendor cannot tell us.

### The policy is a second link, and it unblocks valuations

`sync_client` now creates the account's POLICY (`cam_createwrapper`, which the vendor titles
"Create Investment") after its scheme, and records it as `platform.links(link_type='policy')`
beside the scheme's `primary`. **The two identifiers are not interchangeable**: every scoped action
— contributions, transfers, benefits — is addressed by the `MLSIPP/…` scheme reference, while a
VALUATION is filed against the policy number and `cam_createpolicyvaluation` accepts nothing else.

Step 3 did not exist, so nothing in the estate had ever recorded a policy number — which is why
`services/morgan-lloyd/models/sink_valuations.py::_resolve_valuation` returns `None`
unconditionally and every scheme's ledger row on every nightly run reads
`skipped / no policy reference or fund splits`. The **per-account** push is built
(`app/integrations/morgan_lloyd/valuations.py`, `POST /crm/accounts/{uid}/morgan-lloyd/valuation`);
**the nightly whole-book sink is still unwired**, deliberately, because it runs in the warehouse
process where `platform.links` is not reachable — a chain the pipeline has to grow, not a mapping.

**The policy number is OURS to mint.** The vendor documents it only as "the unique policy number
for the application", so it is the account's own `uid` — already unique, already public (a serial
must never leave this estate), already the identifier an operator can line the two systems up by.
The response "echoes the policy number back as `wrapper`", so the echo is CHECKED when present and
the sent value is what gets recorded: a vendor that stops echoing must not cost us the link to a
policy it did create, and one that echoes something different must not have either value recorded.

### The scheme-scoped actions now scope themselves

Nothing was removed. Every model in `raw_scoped.py` was scoped by `ML_SYNC_SCHEME_REFS`, which was
never set in any environment, so each one looped over an empty list and wrote an empty table. The
scope now **derives itself** from `cam_schemerefs` (7 refs in the dev CRM); the env var survives as
a narrowing override for a targeted backfill.

`raw.scheme_detail` is the `cam_getscheme` view — the crystallised/uncrystallised split — and is
named apart from `raw.schemes`, which is the whole `cam_schemes` collection. Two names because they
are two shapes: a collection is the table as stored, an action is a projection the CRM chose to
expose. The collection is the spine; the action is the detail.

**A per-ref failure is tolerated, counted and logged.** `cam_schemerefs` is a wider list than the
actions accept — 7 refs against 3 live schemes — and `cam_getscheme` answers
`Scheme DSIPP/10372 not found` for the difference. Abandoning the run on the first miss would put
us straight back to writing an empty table, so the refs that resolve still land and the rest are
reported:

```
cam_getscheme: 6 of 7 scheme refs did not resolve: DSIPP/10372, HISIPP/00645, …
```

`cam_getaccounttransactions` currently resolves **none** of the 7, so `raw.bank_transactions` is
still empty — but now for a stated reason rather than silently. Worth asking Clifton whether that
action expects something other than a scheme reference.
