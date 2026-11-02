# API Integrations

What each custodian's API can actually do, which endpoint does it, what each call needs, and
where that value comes from in our schema — or, where it does not exist yet, where it should go.

This is a **contract reference**, not a design document. The subsystem docs own the design:
`docs/contributions.md` (the money ledger), `docs/provider-routing.md` (the delivery queue),
`docs/provider-pipeline.md` (the read side), `docs/platform-model.md` (the conformed shape),
`docs/sei.md` (the SEI file drop), `docs/morgan-lloyd/` (the CRM, CTC XML and endpoint coverage).
Read this one when you need to know _which call, with which fields, from which column_.
The closed personal lists — title, gender, marital status, employment, source of wealth,
source of funds, tax relief, vulnerability, FATCA — and each provider's codes are in
[`docs/client-vocabularies.md`](client-vocabularies.md).

**Two things govern everything below.** First, the vendor clients live in `pkgs/sei`,
`pkgs/morgan-lloyd` and `pkgs/seccl`, each grouped by capability with reads alongside its writes
(§3) and each with a tripwire asserting every path it names is real (§3a). Second, for SEI the
**nightly SFTP drop is the source of record and the API is the exception** — the drop carries the
book and a 183-code-set reference dimension, and reaching for an API call for something the drop
already holds is the most common mistake in this area (§5).

---

## 1. The specs

| Provider             | File                                                           | Form                                                                                      | Size                                                               |
| -------------------- | -------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| SEI                  | `pkgs/sei/spec/sei.openapi.json` (+ `.yaml`)                   | OpenAPI 3.0.0 — "SEI Wealth Platform — Unified API (merged)", version `merged-2026-06-26` | 249 paths / **265 operations** / 2,778 component schemas           |
| SEI (single service) | `pkgs/sei/spec/swagger-spec-CreateOrMaintainCashReceipts.yaml` | Swagger 2.0, one service                                                                  | the cash-receipt source spec                                       |
| Seccl                | `pkgs/seccl/spec/seccl.postman_collection.json`                | **Postman collection v2.1** — Seccl publishes no OpenAPI document                         | **377 requests**, 205 example bodies                               |
| Morgan Lloyd         | `pkgs/morgan-lloyd/spec/ml.openapi.json`                       | OpenAPI 3.1.0, version `1.57`                                                             | 93 paths / **94 operations**, 0 component schemas (bodies inlined) |

Three things to know before reading any of them.

**The SEI spec is a merge of 194 vendor service specs**, one spec per service, so component
schemas are namespaced `<Service>__<Name>` (207 same-name/different-shape collisions across
services made that necessary) and paths are prefixed with the service's base-path segment. The
original path and server survive per-operation in `x-sei-*` extensions. Two more extensions
matter and are used throughout this document:

- `x-sei-onboarding` — the phase SEI groups the operation into (`1. Enrollment & Account
Opening` … `8. Communication & Consent Setup`). 130 of 265 operations carry one.
- `x-sei-enabled` — **75 of 265** are marked enabled. This is a broader set than our contracted
  one (below) and the two do not agree; where they differ, treat the API sheet as authoritative
  and `x-sei-enabled` as a hint.

The spec's `servers` list the plain gateway (`https://api.seic.com/api/v1/swp`,
`https://test.api.seic.com/api/v1/swp`). **We call the mTLS hosts**, which the spec does not
list: `pkgs/sei/src/sei/settings.py::ENVIRONMENTS` maps `i10`/`i31` →
`https://test.mtls.api.seic.com/api/v1/swp` and `p04` → `https://mtls.api.seic.com/api/v1/swp`.
Do not copy a server URL out of the spec.

**The Seccl file is a Postman collection, and that is not a naming accident.** Seccl publishes no
OpenAPI document and no JSON Schema — that is the whole reason
`pkgs/seccl/scripts/build_schemas.py` exists, merging the collection's 205 example bodies into one
JSON Schema per resource and generating pydantic models from those (`pkgs/seccl/README.md`). It
briefly carried an `.openapi.json` extension and now does not, which is right: a reader who trusts
the extension gets a `{info, item, event}` object with no `paths`. Requiredness in the generated
schemas is _inferred_ ("present in every example") and is decent evidence, not a guarantee —
`app/platform_contracts.py` deliberately does not enforce it for Seccl.

**The Morgan Lloyd spec is generated, not written** — `scripts/introspect_api.py` reads the org's
own `$metadata` for what exists and how it is spelled, `scripts/enrich_catalogue.py` scrapes the
published docs for requiredness and option-set meanings, `scripts/build_openapi.py` merges them.
Regenerate it; never hand-edit. **39 of its 94 operations are tagged `Undocumented`** — present
in `$metadata`, absent from the published docs, so parameter names and types are known and their
semantics are not. Its declared server is the **dev** org (`cliftonassetdev.crm11.dynamics.com`).
`POST /Pages/Remote.aspx` is in the same document but is not Dataverse at all: it is the CTC
illustration engine on its own host (`https://camtest.ctc.uk.com`, production `cam`), carried
here for convenience and marked with its own `servers` block.

### 1a. Every SEI service is enabled

SEI has provisioned the whole surface, so the merged spec's 265 operations are all callable and
nothing in this document is gated on entitlement. Two artefacts still say otherwise and should
be read as history rather than as a limit:

- **`x-sei-enabled`** marks 75 of the 265 operations in `sei.openapi.json`. Ignore it.
- **`pkgs/sei/src/sei/vendor_manifest.json`** carries the 26 services from the per-firm API sheet
  (`Select-Marlborough_API_sheet_v2 151025.xlsx`) — the same table this document used to hold
  pasted in. It is still useful for what it is: the vendor's own field lists for those 26, which
  is what `app/platform_contracts.py` reads. It is not the boundary of what we may call.

What that changes: transfers (§4.7), fees (§4.8), the SIPP account and BCE services (§4.4),
recurring-event retrieval and activity cancellation (§4.3), model creation and the third
assignment variant (§4.5), and every reference-data lookup are all in scope. The remaining
constraints in this document are of two other kinds — **the vendor's own data** (the i31
environment defines no pension WRAPPER though it holds 830 SIPPs as classified
Investment Accounts, §5.2) and **our schema** (§6).

One casing trap on the API sheet, worth keeping because it is live: the sheet names
`getaccountdetailswithdsrfeegoalservice/`**`getAccountDetailsInfo`**, and SEI's own Swagger says
**`getAccountDetailsinfo`** — lower-case `i`. `x-sei-original-path` confirms the spec's spelling;
the manifest carries the sheet's, so anything generated from the manifest will 404.

**A second, inverted, casing trap on the same gateway (22 Sep 2026).** Account-template listing
is documented as `GET /enrollmentAccountTemplates/crm/account/templates` (camelCase `A`, from
the swagger file's `servers.url`). That path 404s `Unable to identify proxy for host:
sei-default`. The sibling client service is documented — and deployed — as all-lowercase
`/enrollmentclienttemplates/…`, and that is the proxy name Apigee actually has for both.
`GET /enrollmentaccounttemplates/crm/account/templates` is 200. `pkgs/sei/tests/test_endpoint_paths.py`
now refuses the camelCase spelling so a "follow the spec" edit cannot restore the 404.

`pkgs/sei/tests/test_endpoint_paths.py` checks every path this client calls against the spec,
which is how that class of mistake gets caught rather than discovered live.

## 2. Transport and auth

|                  | SEI                                                                                            | Seccl                                                                                                                                                       | Morgan Lloyd                                                        | CTC                                                                                       |
| ---------------- | ---------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Base             | `https://{test.}mtls.api.seic.com/api/v1/swp`                                                  | `https://pfolio-api-staging.seccl.tech` (prod differs)                                                                                                      | `https://{org}.crm11.dynamics.com/api/data/v9.1`                    | `https://cam{test}.ctc.uk.com/Pages/Remote.aspx`                                          |
| Transport        | **mTLS** — PKCS#12 bundle SEI issues; also an **IP allowlist**                                 | TLS                                                                                                                                                         | TLS                                                                 | TLS                                                                                       |
| Token            | OAuth **password** grant → bearer                                                              | OAuth **client_credentials**; production uses `private_key_jwt` (Auth0 M2M, registered public key + `kid`), staging `client_secret`, legacy `/authenticate` | Azure AD **app-only** token for the Dataverse resource              | **none** — `?wci=RunQuote&nodb=true`, caller identified by `<agent_name>` in the document |
| Third credential | `AppKey` header on every call incl. the token request; the app pair also goes in as HTTP Basic | —                                                                                                                                                           | —                                                                   | —                                                                                         |
| Token life       | ~12 h (`expires_in` 43199)                                                                     | 2 h — **reuse it**, Seccl asks explicitly                                                                                                                   | per Azure AD                                                        | n/a                                                                                       |
| Settings         | `pkgs/sei/src/sei/settings.py` — `SEI_{ENV}_{FIELD}` then `SEI_{FIELD}`                        | `pkgs/seccl/src/seccl/settings.py` — `SECCL_*`                                                                                                              | `pkgs/morgan-lloyd/src/morgan_lloyd/settings.py` — `MORGAN_LLOYD_*` | `CTC_ILLUSTRATION_URL`, `CTC_AGENT_NAME`, `CTC_ENABLED`                                   |
| Idempotency      | none; `externalReference` / `externalSystemTxnId` are ours to set                              | `POST /` with an idempotency key (collection: _Getting started → Idempotency_)                                                                              | **none, and no delete**                                             | n/a                                                                                       |

Two traps worth restating because both have cost real time:

- **CTC answers `text/xml` only.** Send `application/xml` and the engine returns an HTML error
  page with **HTTP 200**, so a client that trusts the status code reports an illustration that
  does not exist. Pinned in `apps/api/app/integrations/ctc/client.py`.
- **A settings class must read both `.env` and `.env.local`.** `MorganLloydSettings` declared
  `env_file=".env"` alone and silently built unconfigured objects from credentials sitting exactly
  where `.env.example` says to put them. Fixed, and pinned by
  `pkgs/morgan-lloyd/tests/test_settings_env_files.py`. `SecclSettings` declares both (untested);
  `SeiSettings` declares `.env` alone and gets away with it only because `cred()` reads
  `os.environ` first.

---

## 3. What the clients expose

Three packages, each restructured to the same shape: a transport base, endpoint mixins grouped
**by capability rather than by vendor URL prefix**, and a composed client. Grouping by capability
is not cosmetic — Seccl's `/portfoliotransactiongroup` alone is contributions, withdrawals,
transfers, fees and trades, so a module per prefix would put five unrelated jobs in one file and
scatter the pension journey across four.

| Package             | Module                        | What it covers                                                        |
| ------------------- | ----------------------------- | --------------------------------------------------------------------- |
| `pkgs/sei`          | `endpoints/reference`         | enrolment templates, DSR catalogue, system code sets, strategies      |
|                     | `endpoints/enrollment`        | the enrolment journey + status, worksheet submit, add owner           |
|                     | `endpoints/clients`           | party read, profile + contact maintenance, delivery preferences       |
|                     | `endpoints/accounts`          | account + characteristics reads, rename, status, DSR tags, portfolios |
|                     | `endpoints/cash_receipts`     | receipts — one-off, anticipated, direct debit, standing order         |
|                     | `endpoints/cash`              | disbursements, cash transfers, pay-to and payer recipient profiles    |
|                     | `endpoints/investment_models` | the three PME assignment variants, de-assign, groups, strategy detail |
|                     | `endpoints/transfers`         | custody transfer in/out, free receipt/delivery, pending status        |
|                     | `endpoints/fees`              | package reads, account + group assignment, fee groups, ad-hoc fees    |
|                     | `endpoints/holdings`          | real-time holdings, net available balance, transactions, tax lots     |
|                     | `endpoints/activities`        | activity state, open activities, cancel, recurring scheduled events   |
| `pkgs/morgan-lloyd` | `endpoints/contacts`          | contact create/read/update, adviser contacts, ATR                     |
|                     | `endpoints/schemes`           | scheme create/update, adviser firm contact, beneficiaries             |
|                     | `endpoints/transfers`         | transfers and pension contributions                                   |
|                     | `endpoints/benefits`          | member details, benefit application, drawdown, documents, messaging   |
|                     | `endpoints/factfinds`         | fact-find, assets, liabilities, relationships, notes, tasks           |
|                     | `endpoints/valuations`        | **rewritten** — typed policy valuations (see §4.9)                    |
| `pkgs/seccl`        | `endpoints/clients`           | clients, accounts, client products, scheme products, nodes            |
|                     | `endpoints/transactions`      | the money rail, transfer counterparties, amend/cancel, book costs     |
|                     | `endpoints/models`            | discretionary + advisory models, rebalance, asset universe            |
|                     | `endpoints/pensions`          | crystallisation (single and regular), next payment date               |
|                     | `endpoints/fees`              | fee register, client fee groups, cash/stock postings                  |
|                     | `endpoints/illustrations`     | product/trade/SMPI illustrations, valuations, client documents        |

**Every module ships GETs alongside its writes**, and that is load-bearing rather than tidy: all
three vendors' reporting path is a batch feed (SEI's nightly drop, Seccl's webhooks and paging),
so without a read there is no way to confirm a write landed until the next cycle — and "did that
work?" is the question that matters most on a money instruction. The reads worth knowing:

- SEI `party_details` returns the client **with its owned accounts**, so it answers "did the
  enrolment land and is it attached to the right person" in one call.
- SEI `account_characteristics` returns the account's **DSR grants and fee packages** — the two
  things a new account has to be given.
- SEI `activity(id)` is how a lodged receipt or disbursement is followed; `activities(account)`
  finds one whose id we failed to record.
- Seccl `live_account_summary` is the figure a withdrawal must be tested against; the plain
  summary is as at the last valuation point.
- Seccl `transaction_group(link_id)` is the only way to see what a group actually created.

### 3a. Path tripwires

Each package now asserts that every path or action it names is real, checked against the vendor's
own artefact:

| Test                                            | Checks                                         | Against                              |
| ----------------------------------------------- | ---------------------------------------------- | ------------------------------------ |
| `pkgs/sei/tests/test_endpoint_paths.py`         | 79 `self.request(...)` calls — method and path | `spec/sei.openapi.json`              |
| `pkgs/morgan-lloyd/tests/test_actions_exist.py` | 49 `call_action(...)` names                    | `spec/ml.openapi.json` (`$metadata`) |
| `pkgs/seccl/tests/test_endpoint_paths.py`       | every path the mixins build, method and shape  | `spec/seccl.postman_collection.json` |

They are static AST scans rather than call-time assertions, which is the point: most of these
methods are money instructions nobody fires in CI, so a mocked test cannot cover them — and a
mock answers whatever it is asked, which is exactly how a placeholder action shipped and stayed
(§7). They earned their keep on the first run: the SEI scan confirmed all 79 paths, the ML scan
found `cam_payrequest` (not in the org — removed), and the Seccl scan found a path _I_ had just
written with the firm segment in the wrong place.

### 3b. Capability matrix

`built` = a code path exists and is reachable from a portal surface. `client` = a typed method
exists in `pkgs/*` and nothing calls it yet. `no vendor support` = the vendor does not offer it.

| Capability                      | Seccl                                    | SEI                                                                                     | Morgan Lloyd                                     |
| ------------------------------- | ---------------------------------------- | --------------------------------------------------------------------------------------- | ------------------------------------------------ |
| Create client                   | client                                   | client (enrolment)                                                                      | **built** (`sync-to-morgan-lloyd`)               |
| Create account, new client      | client (2 calls)                         | client (1 call — `clientList` + `accountList` together)                                 | **built** (`cam_createscheme`, via sync)         |
| Create account, existing client | client                                   | client (`clientList[].existingClient`)                                                  | **built** (`cam_createscheme`, via sync)         |
| One-off payment in              | client                                   | **built** (ACR — `app/integrations/sei/cash_receipts.py`)                               | **built** (`cam_createcontribution`)             |
| One-off withdrawal              | client                                   | client (recipient profile first)                                                        | client (`cam_drawdownsetup`)                     |
| Regular payment in              | client (account `recurringPayments`)     | client (`processingScheduleDetails.recurring`)                                          | client (`cam_frequency`)                         |
| Regular withdrawal              | client (`recurringPayments` + disinvest) | client (disbursement + `recurring`)                                                     | client (`cam_drawdownsetup` + `incomeFrequency`) |
| Cancel a standing instruction   | client (`request_deletion`)              | client (`cancel_activity` — the ONLY way, §4.3)                                         | client (`cam_canceldrawdown`)                    |
| Pension drawdown                | client (crystallisation)                 | client (`crm/sipp-accounts`) — **no pension wrapper in i31; SIPPs are classified 157s** | **built** (`drawdown-session` → DCube SSO)       |
| Attach / change model           | client (create-time only, §4.5)          | client (three PME variants)                                                             | no vendor support                                |
| Illustration                    | client (`/illustration/product`)         | no vendor support                                                                       | **built** (CTC → `cam_illustration`)             |
| Transfer in / out               | client                                   | client (custody transfer + free receipt/delivery)                                       | client (`cam_createtransfer`)                    |
| Fees — assign / ad-hoc          | client (PATCH `feeStructures`)           | client (package + group assignment, ad-hoc)                                             | no vendor support (§4.8)                         |
| Valuations out                  | n/a (source)                             | n/a (source)                                                                            | client (`cam_createpolicyvaluation`, §4.9)       |

---

## 4. Functional areas

Each section gives the calls in the order they must be made, then a mapping table
(`vendor field → our source`), then what is missing. A **bold** entry in the source column is a
gap: nothing in the schema holds it.

### 4.1 Create clients and accounts

#### Seccl — two calls, client first

```
POST /client                       → clientId
POST /account                      → accountId   (requires clientId)
GET  /clientproduct/{firm}/{id}    → clientProductId, for declarations
PATCH /clientproduct/{firm}/{id}   → accept ISA/JISA/Joint-GIA/pension terms
```

An account cannot be created without a `clientId`, so "new client and account together" is two
calls with a hard ordering. The one exception is `POST /illustration/product` with a `clientInfo`
block, which mints a client as a side effect of illustrating (collection: _Client onboarding →
Create a client through an illustration_), then `PUT /client/{firm}/{id}` promotes
`Registered` → `Active`.

`accountType` is always `Wrapper`; the product is `wrapperDetail.wrapperType`
(`GIA`/`ISA`/`JISA`/`PENSION`). A pension additionally needs
`wrapperDetail.schemeProductId` — read it from `GET /schemeProduct/?schemeName=…`.

| Seccl field                                                | Our source                                                                                                                                                                                                                                                         |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `firmId`                                                   | `platform.tenant_platforms.config.firm_id` (provider `seccl`)                                                                                                                                                                                                      |
| `nodeId`                                                   | On create only. The lead adviser's Seccl node (`auth.users.provider_orgs`), or the client's existing node when opening another account, or the firm's only enabled node when the adviser has not chosen. A payment for a client already at Seccl does not send one |
| `clientType`                                               | `crm.contacts.type` (`individual`→`Individual`, `organisation`→`Corporate`/`Trust` via `sub_type`)                                                                                                                                                                 |
| `title`/`firstName`/`surname`                              | `crm.contacts.title`/`first_name`/`surname`                                                                                                                                                                                                                        |
| `dateOfBirth`                                              | `crm.contacts.dob`                                                                                                                                                                                                                                                 |
| `addressDetail`                                            | `crm.contacts.address` jsonb                                                                                                                                                                                                                                       |
| `email`/`mobile`                                           | `crm.contacts.email` / `mobile`. Seccl's `phone` object is read into `crm.contacts.phone` on the way in and is not sent back — the client payload has one mobile                                                                                                   |
| `nationality`, `nationalities[]`                           | `crm.contacts.nationality[]` + `national_identifier`/`_type`                                                                                                                                                                                                       |
| `nationalInsuranceNo`                                      | `crm.contacts.national_identifier` (where `_type='NINO'`)                                                                                                                                                                                                          |
| `taxDomicile`                                              | `crm.contacts.tax_residency[0]`                                                                                                                                                                                                                                    |
| `vulnerability.{isVulnerable,reason[],isSupportTemporary}` | `crm.contacts.is_vulnerable` / `vulnerability_reasons[]` / `vulnerability_is_temporary` — **the vocabularies already match**: Seccl's four reasons are the FCA FG21/1 drivers 0054 stores                                                                          |
| `bankDetails`                                              | `crm.contacts.bank_account` jsonb                                                                                                                                                                                                                                  |
| `kycData.{domesticPep,sanctioned,…}`                       | **not held on the contact.** `aml.*` holds screening outcomes; see the note at the end of this section                                                                                                                                                             |
| `amlStatus`, `termsAccepted`, `emailVerified`              | **not held**                                                                                                                                                                                                                                                       |
| `feeStructures.adviserFeeId`                               | `platform.fee_packages.external_id` (see §4.8)                                                                                                                                                                                                                     |
| account `name`                                             | `platform.accounts.name`                                                                                                                                                                                                                                           |
| account `currency`                                         | `platform.accounts.currency`                                                                                                                                                                                                                                       |
| `wrapperDetail.wrapperType`                                | `platform.products.product_type` via `platform.accounts.product_id`                                                                                                                                                                                                |
| `wrapperDetail.discretionary`/`advised`                    | `platform.accounts.is_discretionary`; `advised` from `crm.contacts.service_type`                                                                                                                                                                                   |
| `wrapperDetail.assetAllocationId`                          | `platform.models.platform_identifier` — **but see §4.5**: this is create-only                                                                                                                                                                                      |
| `wrapperDetail.schemeProductId`                            | `platform.product_platforms.account_open_reference` (0181 — §5.4)                                                                                                                                                                                                  |
| `wrapperDetail.nominations[]`                              | **not held** — no beneficiary table exists                                                                                                                                                                                                                         |
| `productDetail.flexibleBenefitsAccessed`                   | `crm.contacts.flexi_access_taken`                                                                                                                                                                                                                                  |
| `productDetail.flexiAccessDate`                            | **not held** — 0053 stores the boolean, not the date                                                                                                                                                                                                               |
| `recurringOrders`, `recurringPayments`                     | see §4.3                                                                                                                                                                                                                                                           |

#### SEI — one call, both together

```
POST /accountservice/accounts                                → processInstanceId, businessKey (ENR-…)
GET  /enrollmentstatus/enrollment/{firmId}/{bpmProcessInstanceId}
                                                             → accountNumber, globalIdOfAccount,
                                                               globalIdOfPortfolios[], primaryEndClientId
POST /submitenrollmentservice/submitEnrollment                (worksheets left "Work In Progress Complete")
PATCH /enrollment/enrollment/completeEnrollmentEnrichmentByActivity/{activityId}
```

The body carries `clientList[]` and `accountList[]` in one request, so new-client-plus-new-account
is a single call. An existing client is `clientList[].existingClient`
(`clientExternalPartyId` or `clientEntityId`); an existing third party becomes a client with
`newClient.existingThirdParty = true`. Adding an account to an already-open account's ownership
is a separate service, `POST /addaccountownerservice/accountowner` — `EnrollmentApi.add_account_owner`.

**This is not a form.** The body is eight levels deep with two arrays and 147 leaves fully
expanded, which is why `app/platform_actions.py` deliberately declines to offer it as a drawer.
It is a journey. (Its comment says 48 leaves — that is the API sheet's own flattening, which
counts the arrays once rather than expanding them. Both numbers are right about different
documents.)

Required by the vendor (`*` in the spec):

| SEI field                                                               | Our source                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `data.attributes.firmId`                                                | `platform.tenant_platforms.config.firm_id`                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `data.attributes.usedBy`                                                | literal `Graphene` — **mandatory** (code 2196); the cash paths already default this                                                                                                                                                                                                                                                                                                                                                                             |
| `data.attributes.bypassApproval`                                        | literal `true` — skip the configured approval step when the request qualifies for straight-through processing                                                                                                                                                                                                                                                                                                                                                   |
| `clientList[].newClient.existingThirdParty`                             | literal `false`                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `…newClient.externalPartyId`                                            | **`crm.contacts.uid`** — recommended: a 12-char nanoid is exactly the "external source system reference" SEI wants, and it makes the SEI→book join reversible without a link row for the _first_ call                                                                                                                                                                                                                                                           |
| `…newClient.residingOrgId`                                              | `platform.tenant_platforms.config.firm_id`. On 3106 the **client** sits at the firm; the **account** org is `config.custody_orgs[account.custody]` (onshore → 704254). Swapping those is 2345 / 2048. A firm with no subfirms (3102) sends no account org.                                                                                                                                                                                                      |
| `…newClient.clientTypeId`                                               | `5` Individual / `6` Organization ← `crm.contacts.type`                                                                                                                                                                                                                                                                                                                                                                                                         |
| `…newClient.clientTemplateId`                                           | `config.client_template_individual_id` or `config.client_template_organisation_id`, by `crm.contacts.type`. `config.client_template_id` is the individual choice and the fallback when neither typed key is set. Discovered by `ref.enrollment_templates` (§5.4). A firm with exactly one template fills both sides.                                                                                                                                            |
| `…individualDetails.firstName`/`lastName`/`dateOfBirth`                 | `crm.contacts.first_name`/`surname`/`dob`                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `…individualDetails.certifiedFATCAComplianceId`                         | `crm.contacts.fatca_status` (0134) → `1` W-9 / `2` W-8                                                                                                                                                                                                                                                                                                                                                                                                          |
| `…address.isoCountry`/`addressLine1`/`addressLine2`/`city`/`postalCode` | `crm.contacts.address` jsonb. Country defaults to GB. A GB address without a postcode is refused. Line 2 and the postcode are sent when the fact-find holds them                                                                                                                                                                                                                                                                                                |
| `…phone.phoneMethodId` + `localPhoneNumber`                             | `crm.contacts.mobile` as `PHONE_METHOD` **2** (Mobile), else `crm.contacts.phone` as **1**. The subscriber number only — the address country is the prefix. Enrolment takes one phone                                                                                                                                                                                                                                                                           |
| `…email.emailAddress`                                                   | `crm.contacts.email`                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `accountList[].accountTypeId`                                           | `platform.products.product_type` → `157` GIA / `172` ISA / `173` JISA. This is the inverse of the mapping `pkgs/book-sink` already reads (`docs/sei.md`: `ACCT_TYPE` 157→GIA, 172→ISA, 173→JISA, 176 House Account unmapped)                                                                                                                                                                                                                                    |
| `accountList[].accountSubTypeId`                                        | `1` Investment Account (§5.2)                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `accountList[].accountClassificationId`                                 | **§5.2** — `48518` Individual in the verified sample; the workbook lists only 48519–48525                                                                                                                                                                                                                                                                                                                                                                       |
| `accountList[].firmInvestmentCapacityId`                                | `platform.accounts.investment_capacity` → integer `3` Discretionary / `4` Advisory / `5` Execution Only. `retrieveAccountDetails.accountBasicsDetail.firmCapacity` is a different field (example `Custodian`) and is not this code                                                                                                                                                                                                                              |
| `accountList[].accountTemplateId`                                       | `platform.product_platforms.account_open_reference` (0181 — §5.4)                                                                                                                                                                                                                                                                                                                                                                                               |
| `accountList[].accountName`                                             | `platform.accounts.name`                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `accountList[].accountBaseCurrency`                                     | `platform.accounts.currency`. When the firm allows more than one currency, `account.currencies` runs after the pay-to step and adds the others with `addAllowableCurrencyToAccount` (`allowTransactionLevelOverride: "true"`), one code at a time. The base is not sent again. A code SEI will not add (`Invalid iso currency code`) is left off and does not fail the enrolment.                                                                               |
| `{client,account}List[].teamServicingRelationshipList[]`                | **§5.3** — the teams named on the firm (`config.dsr_ids`, plus a custody's `custody_dsrs`, plus `config.model_dsrs` for a model the account uses). The relationship id is the one that scope already uses for that team                                                                                                                                                                                                                                         |
| `{client,account}List[].employeeServicingRelationshipList[]`            | the employee the firm's latest full client/account file grants on that relationship. A thinner file delivered later is not that book. The journey's adviser replaces them only when `auth.users.provider_refs->>'sei'` is already a holder of that same relationship. The Team page chooses that id from the book's employee grants (SEI's id and name), not from office teams or other logins. An employee id that has never held the relationship is not sent |
| `accountList[].accountOwner.primaryOwnerExternalId`                     | the same `crm.contacts.uid`                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `accountList[].portfolios[].principalPortfolio.portfolioName`           | **not held** — we have no portfolio entity in OLTP; §6                                                                                                                                                                                                                                                                                                                                                                                                          |

Sent when the book holds a value SEI has a code for: `titleId` (`crm.contacts.title` →
`1` Mr, `2` Mrs, `3` Ms, `4` Dr, `21` Miss, `12` Prof), `maritalStatusId` (`marital_status` →
`1` Married, `2` Never Married, `8` Divorced, `10` Widowed), `clientAMLRiskRating` (ComplyCube
`compliance.client_risk.overall`: `1` low, `2` medium, `3` high, and `very_high` as high) with
`clientAMLRiskLastReviewDate` from that profile's timestamp, clamped to SEI's current day (US Eastern) so a review just after midnight in the UK is not sent as a future date. A title, status or level with no
code is left off. `genderId` is sent (`1` male / `2` female). SEI documents that enrolment field as no longer shown, so the profile update also sends `gender` as the same integer. Occupation (free text, 100 characters) and a source of funds SEI has a code for go on that profile update after the enrolment completes. Town of birth is a tax-residence field and is not sent. Source of wealth has no SEI field.
Also sent: `countryOfBirth`
(`crm.contacts.country_of_birth`, ISO), `countryOfCitizenshipFirstPassport` (`nationality[0]` / `passport_country`),
`taxInformation.identifier.{identifierISOCountry, taxIdTypeId, taxIdNumber}`
(a UK National Insurance number is `taxIdTypeId` **7** and `identifierISOCountry` **GB**, from
`national_identifier` when that type is a NINO, otherwise from `data.uk_national_insurance`).
An ISA or a pension always needs one. A GIA needs one when nationality or tax residency
includes the UK. The fact-find does not ask a second time when the nationality identifier is
already a National Insurance number. `clientRiskRatingId` (**not held at client level** —
`risk_level` is on the account). `mailingLabelName` is required by the BPM even though the spec marks it optional —
it belongs on `individualDetails` (a `newClient`-level copy is silently ignored, then code
2436).

#### Morgan Lloyd — adviser, contact, then a scheme per pension account

```
POST /cam_CreateFinancialAdviserContact → adviser GUID (on `auth.users.provider_refs`)
POST /cam_createcontact                 → contact GUID (nested under `response`, NOT a top-level contactid)
POST /cam_createscheme                  → MLSIPP/nnnnn
POST /cam_createwrapper                 → the policy ("Create Investment")
POST /cam_createbeneficiaries           → nomination GUID (per `crm.beneficiaries` row)
```

`app/integrations/morgan_lloyd/sync.py` does adviser-then-contact-then-scheme-then-investment-then-beneficiaries, idempotently
(each step checks its recorded identifier first and reports `skipped`), reached from the SIPP
account record's "Sync to Morgan Lloyd" action and from the client record's matching action. `cam_createcontact` requires **12** of its 35 fields:
`firstname`, `lastname`, `cam_birthdate`, `gendercode`, `cam_title`, `cam_nationality`,
`cam_ninumber`, `emailaddress1`, `address1_line1`, `address1_city`, `address1_postalcode`,
`as_contactstatus`. All 12 are held (`gendercode` since 0052 — its absence is what made drawdown
unreachable for every client). `MORGAN_LLOYD_ADVISER_FIRM_ID` must be set and must be an entity
reference (`/accounts(<guid>)`, not a bare GUID) or the contact is unattached and a later
`cam_createscheme` against it is refused.

`cam_createscheme` itself is open. It answered `403 0x8004f502` on a ML Team secured field from
09 Aug 2026, including pairings this principal had created on 30 Jul and 3 Aug. Clifton granted
the field security on 05 Sep 2026. The live lifecycle suite then created `MLSIPP/02113` and
`MLSIPP/02114` with `schemeType` 16 and `sippType` 809130016, and `sync.py` calls
`cam_createscheme` for each SEI SIPP. The write-up of the block and the grant is
`docs/morgan-lloyd/endpoint-coverage.md`.

`cam_createwrapper` ("Create Investment") takes `policyNumber` + `schemeReference` + `provider`
and is the prerequisite for §4.9 — a valuation is filed against a _policy_, not a scheme.

> **On sending screening data to a custodian.** Seccl's `kycData`/`amlStatus` and SEI's
> `clientPEP`/`clientAMLRiskRating` are legitimate regulatory disclosures to the custodian and are
> _not_ the tipping-off risk `docs/aml.md` guards against — that control is about the **client**
> reading their own screening position, and the `aml.*` RLS policy enforces it by omitting
> `auth.contact_scope()`. Enrolment sends only ComplyCube's overall level (`1`/`2`/`3`) and the
> review date. The profile breakdown — political exposure, occupation, watchlist — stays off the
> call, and `compliance.client_risk` stays unreadable to the firm.

### 4.2 One-off payments in and withdrawals out

#### Payment in

|       | Call                                                  | Notes                                                                                                                                                             |
| ----- | ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Seccl | `POST /portfoliotransactiongroup`                     | `transactionType: Payment`, `transactionSubType: Deposit`, `movementType: In`, `method: Bank Transfer`/`Direct Debit`. `autoInvest: true` invests on arrival.     |
| SEI   | `POST /currencyreceiptservices/createCurrencyReceipt` | **built.** An ACR is a receipt with a future `once.dueDate` and no `payerInfo`: nothing is collected, SEI expects the money and reconciles the credit against it. |
| ML    | `POST /cam_createcontribution`                        | **built** (`app/integrations/morgan_lloyd/contributions.py`). Records the contribution against the scheme; it does not move money.                                |

The whole lifecycle around these — booking, matching a credit, relief, the ML status mirror — is
`docs/contributions.md`, and the endpoints are already wired: `POST /contributions`,
`POST /contributions/{uid}/lodge-expectation`, `POST /contributions/{uid}/record-to-morgan-lloyd`.

SEI's receipt carries three groups of value it cannot derive — the account's identifiers _at SEI_,
the description/treatment pair, and the firm id — and `cash_receipts.py::missing_fields` names
whichever is unset rather than guessing. Instructing a custodian to expect money on a client's
account is not somewhere to substitute a plausible default.

| SEI field              | Source                                                                                                                                                                                                                  |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `accountNumber`        | `platform.links` (`provider='sei'`, `entity_type='account'`, `link_type='primary'`)                                                                                                                                     |
| `portfolioId`          | `platform.links` (same, `link_type='portfolio'`) — but the source is the nightly `Accounts` report, which is grained account × portfolio and carries `PORTFOLIO_ID` (§5.1). One link row holds only one, which is §6 #9 |
| `descriptionTypeCode`  | `platform.tenant_platforms.config.contribution_description_code`                                                                                                                                                        |
| `treatmentTypeCode`    | `platform.tenant_platforms.config.contribution_treatment_codes.{personal,employer,third_party}`                                                                                                                         |
| `firmId`               | `config.firm_id` — required because our SEI users are Processing-Organisation level                                                                                                                                     |
| `receiptMethod`        | `sei.enums.ReceiptMethod` — BACS `5`, CHAPS-via-SWIFT `6`, cheque `3`                                                                                                                                                   |
| `receiptAmountType`    | `1` Gross Value, or `4` Unknown for an ACR whose size is not yet known                                                                                                                                                  |
| `treatmentCodeTaxYear` | `platform.contributions.tax_year`                                                                                                                                                                                       |
| `externalSystemTxnId`  | our own reference — **use it**; SEI has no idempotency key and this is the only handle for a duplicate hunt                                                                                                             |

The description/treatment pair used to be a standing blocker. It is not: the nightly Reference
report carries both sets in full and `app/integrations/sei/reference.py` holds the UK RAS pair
(treatment 4958–4963 ↔ description 21121–21126, plus 20009 Contribution, 20012 Transfer In). The
422 now _names_ the code to configure. It stays a suggestion, never a substitution, because SEI
validates the pair against the caller's role (error 1006).

#### Withdrawal

|       | Call                                                        | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ----- | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Seccl | `POST /portfoliotransactiongroup`                           | `transactionSubType: Withdrawal`, `movementType: Out`, `method` from the instruction's chosen rail: `bacs` → `BACS Credit` (the default). The vendored collection observes only `BACS Credit` / `Bank Transfer` / `Bulk Bank Transfer` / `Direct Debit` / `Transfer` — no Faster Payment, CHAPS or SWIFT — so a withdrawal raised on another rail is **skipped with the reason** (`outbound.UnsupportedRail`), never sent as BACS behind a quoted fee; the row stays pending for a manual send. Seccl has no fee-waiver field: `pass_rail_fee` rides on the outbound row's `data` only. `autoDisinvest: Proportional`/`Largest` sells to fund it; `Raise Trade` (`POST /portfoliotransaction`) is the sell-by-amount primitive. Cancel with `PUT /portfoliotransaction/action/requestdeletion/{firm}/{id}`. |
| SEI   | `POST /currencydisbursementservices/createcashdisbursement` | A **memo** withdrawal (`MemoDisbursement`, no `paymentType`) does not need a pay-to. A **BACS** withdrawal names the pay-to profile enrolment already created (`payToDetails.payToProfileId`). `POST /currencyrecipientforaccountservicesv2service/createpaytorecipientforaccount2` is the `account.pay_to` step after enrolment, once the account is Open, not the withdrawal. `adhocPayToDetails` is the alternative and carries the full bank block. Modify with `…/modifycashdisbursement`.                                                                                                                                                                                                                                                                                                             |
| ML    | `POST /cam_drawdownsetup`                                   | Pension only; see §4.4.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |

SEI's disbursement is the **largest body of all 265 operations** (283 leaves, against the
enrolment's 147) because it carries every payment rail:
`paymentType.{ach,wire,check,bacs,chaps,swift,sepa,fasterPayment}`, each with its own
required sub-block. For UK BACS the mandatory pair is
`bacsDetails.{destinationStatementText, originationStatementText}`; for CHAPS/Faster Payment it is
`{paymentInformation, payeeBankInstructions}`. `amountType` is a one-of:
`netAmount` / `grossValue` (UK) / `grossDistribution` (US) / `disbursementPercent` / `unknown`.
`processingDateOptions.raiseCashMethodOptions` is SEI's auto-disinvest.

On Select i31 (28 Sep 2026) the memo shape from SEI's own example succeeded on an open GIA
(description 20023, treatment 2451, no pay-to). A pay-to profile also created. The BACS
`CurrencyDisbursement` against that profile still dies inside SEI's builder (a system
exception, after the body has parsed). A payer profile is the matching prerequisite for a
Direct Debit receipt only; the memo receipt that succeeded (cheque method `3`, `memoFlag`
true, description 20190, treatment 1303) names no payer. The request `data.type` is
`CurrencyReceiptRequest`.

Our side: `platform.instructions` (0016) holds the client-raised intent
(`kind IN ('expected_in','withdrawal','adhoc_fee','model_topup','model_withdrawal')`) and the withdrawal gate
in `app/instructions.py` refuses when it cannot read holdings from the tenant's lake slice.
`app/outbound.py` (0179) writes a pending `platform.transactions` row for every one of those
kinds, plus booked contributions and transfers, and dispatches best-effort to the account's
custodian. Seccl is `POST /portfoliotransactiongroup` with `externalReference = origin uid`
(a retried group is a second instruction, so a second send is refused once a Seccl link
exists). SEI contributions reuse the anticipated receipt. A one-off withdrawal is a BACS
disbursement. An ad hoc fee is a one-time direct charge. Status mapping:
`docs/transaction-status.md`.
The 0074 instruction queue stays dropped (0077); this is a row on the fact table, not a
retry worker.

An `adhoc_fee` may name **which** fee it is: `platform.instructions.fee_package_id` (0172) points
at one of the firm's own `platform.fee_packages`. The column is optional — the account record's
Ad hoc fee drawer asks only for a Fee amount — and when a type IS sent it must be one this firm
charges on this account. On SEI the amount-only fee uses the firm's one-time initial advice
fee type. A named package is sent only when its name, or its `external_id`, is that firm's
one-time `FEE_TYPE`; otherwise the charge is refused rather than booked as a different fee. `instructions.fee_types_for_account` still draws the applicable list with
exactly the filter `GET /catalog/fee-packages?product_id=&model_id=` uses, because a type the API
accepts that the catalogue would not offer would name a charge this firm does not make. The composite
`(tenant_id, fee_package_id)` foreign key makes tenant containment a schema invariant — this row is
written by `firm_session`, which is `service_role` and unbound by RLS.

`platform.instructions.amount` is `numeric(18,2)`, where `platform.transactions.amount` and
`platform.contributions.amount` are `numeric(20,4)`. Harmless today; worth aligning before
anything computes across the two.

#### Watching an instruction land

SEI: `GET /activitymanagementservices/activity/{activityId}` returns `activityStatus` +
`activityState`. Both code sets are in the i04 workbook (§5.2) — 15 statuses and 431 states. The
states that matter for money are `22` Sent to Payment Processing System, `16` Accepted, `18`
Confirmed, `19` Matching Failed, `29` Instruction Rejected by Custodian, `49` Pending Approval,
`51` Approved, `52` Not Approved, `110` Completed, `111` Unable to Complete.
`GET /activitymanagement/activities` lists every OPEN activity on an account (`ActivitiesApi.activities`) — the only way to find one whose id we failed to record.

Seccl: poll `GET /portfoliotransaction/{firm}/?linkId={groupId}`, or register the transaction
webhook (`PATCH /webhook/config/transaction/{firm}`). Seccl's webhooks are the better answer and
nothing consumes them yet.

### 4.3 Regular payments and withdrawals

This is the thinnest-supported area on our side and the best-supported on theirs.

|       | Call                                                                                             | Shape                                                                                                                                                                                                                                                                                                                                             |
| ----- | ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Seccl | `PATCH /account/{firm}/{id}`                                                                     | `recurringPayments[]` — `{paymentDay, amount, movementType: In\|Out, method, autoInvest}`. Requires the account's current `updateId` (read it first). Plus `autoDisinvest` for a withdrawal that must sell. Investment side is `recurringOrders` — `{movementType: Invest, investmentType: Bespoke, details[]: {assetId, percentage}, orderDay}`. |
| SEI   | the **same** create calls as §4.2, with `processingScheduleDetails.recurring` instead of `.once` | `{startDate, daily{interval,dayType} \| weekly{interval,weekDay} \| monthly{interval,dayType,monthDayNum,resolveOption,monthDayType,monthlyOccurrence}, endAfterCount, endByDate}`. Codes are in `sei.enums` (`DayType`, `WeekDay`, `ResolveOption`).                                                                                             |
| ML    | `cam_createcontribution` `cam_frequency`; `cam_drawdownsetup` `incomeFrequency` + `paymentDay`   | `paymentDay` accepts `6 \| 14 \| 21 \| 28` only. `platform.contributions.frequency` already carries the five real periods for exactly this reason — "regular" alone is unsendable.                                                                                                                                                                |

**A recurring instruction has nowhere to live.** `platform.accounts.payments` and `.withdrawals`
are `jsonb`, the UI already renders them (`RegularMovement` in
`pkgs/ui/src/lib/components/book/accounts/types.ts`: `{amount, frequency, next_date, method,
reference}`) — and **nothing writes them**. No provider read populates them and no write path
exists. A repository-wide search finds `recurringPayments` in exactly three places:
`pkgs/seccl/src/seccl/models/{account,illustration}.py` (generated) and
`apps/api/app/flows/onboarding_seed.py` (a question). So the account record's "regular movements"
band is empty for every firm.

This is the gap with the widest blast radius in the document, because a standing order is a
_mandate_ — it has a start date, an end condition, a next-due date, a provider reference and a
cancellation, and none of that survives in a jsonb blob. §6 proposes `platform.mandates`.

**Reading one back and stopping one are both available, and the second governs the design.**
`ActivitiesApi.recurring_scheduled_events` reads a client's or account's standing instructions;
`ActivitiesApi.cancel_activity` is the **only** way to stop one — SEI has no "delete mandate"
endpoint, because the recurrence lives on the ACTIVITY the create returned. So a mandate we
create must have its `activityId` recorded, or it cannot be stopped through the API at all. That
is what makes `provider_reference` on the mandate row load-bearing rather than bookkeeping.

For reporting, read the drop instead: **`raw.rec_cash_activities` already lands the whole
standing-order population nightly** — 55 columns carrying the recurrence frequency and interval,
the payer and pay-to profile ids, net/gross amount, next activity date and the
description/treatment codes. Nothing conforms it yet, which is why §6 #1 is a modelling job over
data we already receive rather than an integration.

### 4.4 Pension and SIPP drawdowns

#### Morgan Lloyd — built

```
POST /cam_setmemberdetails    → benefitApplicationId + the DCube ssoUrl (token embedded)
POST /cam_drawdownsetup       → the income instruction
POST /cam_schemememberevents  → benefit events (read)
POST /cam_canceldrawdown      → cancel
POST /cam_amenddrawdown       → amend (UNDOCUMENTED: names and types known, semantics not)
```

`POST /crm/accounts/{uid}/drawdown-session` is built and framed in a side drawer on the account
page. A SIPP offers Drawdown, not Withdraw: the cash-form button is hidden and Drawdown mints a
fresh session. It needs **only** `MORGAN_LLOYD_*` — `cam_setmemberdetails` returns the DCube
`ssoUrl` with its token embedded, so the member completes the journey at the vendor. It
addresses the client by their Dynamics GUID and this account's scheme reference from
`platform.links` (what **Sync to Morgan Lloyd** writes). The product shelf's
`scheme_administrator` is not consulted: a SIPP whose product only names a custodian is still
drawable once those links exist. `cam_drawdownsetup` remains the later income instruction (nine
required fields); it is not this button.

`cam_setmemberdetails` requires `contactId` and `advised`. OpenAPI also marks
`illustrationTemplate` optional, and the mint succeeds without it. DCube loads that XML
_after_ SSO (`DrawdownSetup/Illustration/IllustrationDocument`,
`docs/morgan-lloyd/workflows.md` step 3), so we still send the CTC1 document
`services/morgan-lloyd/scripts/generate_illustrations.py` always used (SIPPEN, existing fund,
generic 100% split, maximum tax-free cash — `quotation_type` is `Flexi Access Drawdown` because
plain `Drawdown` is capped). Sex and date of birth are omitted when the client record has none;
they are not invented. The 500 on the SSO URL itself is CTC `SSOLogin`, not a missing
template (`docs/gotchas.md`). Money context (`assetValuation`, `cashValuation`,
`crystallisedValue`) goes with the mint: account value, latest
`platform.valuations.cash_value` (or 5% of the pot when there is none), and crystallised value.
Create-time valuations are ignored by `cam_memberdetails`; the wrapper policy valuation is what
sets `assetValuation`.

`cam_drawdownsetup` requires 9 of 24: `benefitApplicationId`, `drawdownOption`
(2 Full TFC / 3 Partial TFC / 4 Full UFPLS / 5 Partial UFPLS / …), `phasedUFPLS`, `phasedPCLS`,
`investmentPathway` (1–4), `sortCode`, `accountNumber`, `accountName`, `tfcUsed`.

| ML field                                                                                                      | Our source                                                     |
| ------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `sortCode`/`accountNumber`/`accountName`                                                                      | `crm.contacts.bank_account` jsonb                              |
| `tfcUsed` (Lump Sum Allowance used)                                                                           | **not held** — the LSA/LSDBA running total has no column       |
| `investmentPathway`                                                                                           | **not held**                                                   |
| `pensionWiseAccessed`, `guidanceAppointment*`, `adviceAppointment*`, `recentlyReceived*`, `doesNotWantAdvice` | **not held** — nine FCA advice-and-guidance flags with no home |
| `regularIncomeAmount`/`taxFreeIncomeAmount`/`incomeFrequency`/`paymentDay`                                    | §4.3 — the mandate that does not exist                         |

Note the typing trap `app/platform_contracts.py` exists to handle: `guidanceAppointmentAttendance`
is documented as a **string** `"0"`/`"1"` while `cam_previouslytakenflexiaccessbenefits` is the
**integer** `0`/`1`, same two labels. Coercing on the value rather than the documented type swaps
them, and the difference surfaces only as a vendor 400 on a live call.

#### Seccl — endpoint known

```
POST /clientproduct/action/crystallisation          → one Benefit Crystallisation Instruction
GET  /clientproduct/action/{firm}/{id}              → read it back
POST /clientproduct/action/regularcrystallisation   → UFPLS or FAD, recurring
PATCH /clientproduct/action/regularcrystallisation/{firm}/{id}  → cancel
PATCH /clientproduct/{firm}/{id}                    → LTA/LSA figures, protections, nominees
GET  /v1/nextPaymentDate/?currentDate=…&dayOfMonth=…&paymentType=Regular+Crystallisation
```

The crystallisation body pairs `crystallisationDetails` (`type: FAD|UFPLS`, `frequency`,
`fullCrystallisation`, `amount`, `crystallisationDay`, `expiryDate`) with a `withdrawalDetails[]`
array of `PCLS`/`FAD` legs. It needs the same FCA context ML does — `advised`, `guidance`,
`fcaIndividualReference`, `benefitsTakenBefore2006`, `protectedRetirementAgeOrLumpSum` — and
none of the five is held.

Seccl also needs a drawdown **account** (`portfolioGroups[].groupType: Crystallised`), so
crystallising is create-account-then-instruct, not one call.

#### SEI — available, but there is no wrapper to drawdown from

`POST /crm/sipp-accounts` and its sub-resources are a genuinely rich pension profile — `bces[]`
(Benefit Crystallisation Events, with `type`, `amount`, `taxFreeCashLumpSum`,
`pensionLumpSumDeathAllowance`), `protections[]` (Primary/Enhanced/Fixed 2012/2014/2016…),
`mpaaContributionAllowance` + `mpaaEffectiveDate`, `personalContribution`/
`employerContribution`/`thirdPartyContribution`/`pensionPayment` each as
`{amount, frequency, nextDate}` over a fixed six-value frequency enum, plus
`individualLumpSumAllowanceUsed` and `lumpSumDeathBenifitAllowanceUsed` (SEI's spelling).

Two things block it:

1. It is **not on the API sheet** (§1a).
2. **No pension WRAPPER — and that is not the same as no pensions.** `ACCT_TYPE` for this firm is
   157 Investment Account, 172 S&S ISA, 173 S&S JISA, 176 House Account (§5.2, and confirmed
   against the p04 corpus in `docs/sei.md`). The US IRA types 177–183 in the workbook are not
   available to a UK firm.

   The conclusion drawn from that here — "so there is nothing to hold a SIPP" — **was wrong, and
   the drop disproves it.** SEI carries the pension nature in a different code set entirely:

   ```
   ACCOUNT_CLASSIFICATION_NAME      Pension                            934 accounts
   ACCOUNT_SUB_CLASSIFICATION_NAME  Pension - HMRC Approved - SIPP     830
                                    Pension - HMRC Approved - SSAS       4
   ACCOUNT_QUALIFIER_NAME           SIPP                                14
   ```

   Every one of those is `ACCOUNT_TYPE` 157. **A SIPP at SEI is an Investment Account classified as
   a pension** — the firm already holds 830 — which is exactly the custodian-plus-administrator
   split this estate runs: SEI holds the assets, Morgan Lloyd administers the scheme. Nothing in
   SEI's account model names the administrator, because the scheme is not SEI's record to keep;
   `MLSIPP/…` lives on `platform.links`.

   It cost something. `services/sei/models/platform/accounts.sql` mapped `product_key` from the
   wrapper alone, so all 830 SIPPs landed in the book as GIAs — and `product_type` is what every
   "is this a pension?" reader gates on, so those accounts could not be drawn down, could not take
   a pension contribution and were tested against the wrong annual allowance. Fixed, and pinned by
   `services/sei/tests/test_platform_accounts.py`.

   Creating one through the API is the enrolment journey. Live on i31 (22 Sep 2026):
   `client_templates` and `account_templates` both answer 200 (the account-template path is
   all-lowercase `/enrollmentaccounttemplates/…`; the Swagger camelCase 404s — §1).
   `POST /accountservice/accounts` accepts a body (BPM handle `ENR-…`) once `usedBy` is set.
   Completion then fails async validation (DSR composition, `mailingLabelName`, tax /
   citizenship) — that is configuration we do not yet hold, not an entitlement gap.

That `bces[]`/`protections[]`/MPAA shape is a good target for the
schema §6 proposes — it is the most complete statement of the pension facts any of the three
vendors asks for, and it maps onto `crm.contacts.pension_protection[]` and
`flexi_access_taken`. The contribution screens do not test a payment against those facts.

### 4.5 Attach and change a model

#### SEI — PME model assignment (the two contracted variants)

```
POST /pmemodelassignmentservices/PSM/model/assignment/implementation
POST /pmemodelassignmentservices/PSM/model/assignment/allocation
POST …/investment            → InvestmentModelsApi.assign_investment_model
POST …/byAccount             → InvestmentModelsApi.assigned_model  (read)
POST …/delete                → InvestmentModelsApi.remove_model    (de-assign)
```

Every variant takes the same core: `firmId*`, then `accountNumber` **or**
`investmentGroupId`/`investmentGroupName`; `portfolioId` **or** `portfolioName`; `modelId`
**or** `modelName`; `toleranceMultiplier*`; and `eventProcessingId`, which is mandatory for a
_new_ assignment and optional for a change. `apsId` (allowable processing schedule) defaults to
NONE.

| SEI field             | Our source                                                                                                     |
| --------------------- | -------------------------------------------------------------------------------------------------------------- |
| `firmId`              | `config.firm_id`                                                                                               |
| `accountNumber`       | `platform.links` (`sei`/`account`/`primary`)                                                                   |
| `portfolioId`         | `platform.links` (`sei`/`account`/`portfolio`)                                                                 |
| `modelId`             | `platform.model_platforms.external_id` for provider `sei` — the model id the book already holds                |
| `toleranceMultiplier` | `raw.api_models.tolerance_multiplier`, from the assignment read. No published value, and the model is not sent |
| `eventProcessingId`   | `3` (As per Rule, system code 37786). A first assignment requires it, and the firm does not store one          |

Note the duplicate paths: `pmemodelassignmentservices/PSM/model/assignment/{x}` and
`pmemodelassignment{x}/PSM/model/assignment/{x}` are the same operation reached through two
service base paths, an artefact of the merge (§1). The API sheet names the
`pmemodelassignmentservices/` form — use that one.

**Which variant?** `model.attach` sends the path for the type `raw.api_models` published:
`8` investment, `4` implementation, `3` asset allocation. A strategy (`5`) is not one of those
writes, so that model is not sent. An implementation assignment also sends sleeve type `1`
(Model), which applies only on a first assignment. The schedule id is sent when the catalogue
has one, except on an allocation model, where SEI ignores it. The account is read until it is
Open — WIP Submitted included — and then the call goes to the principal portfolio.

Model _creation_ at SEI (`POST /investmentmodel/investmentmodel`,
`POST /savestrategymodel/modelManagement/saveStrategyModel`) is available but deliberately not
wrapped: models are created in the SEI UI and read into our catalogue by the pipeline, and a
second authoring surface would give two places a firm's model can be defined.

#### Seccl

```
POST  /model             → discretionary model
POST  /model/advisory    → advisory model
PATCH /model/{firm}/{id} → update either
GET   /model/{firm}/     → list
POST  /event/            → {eventType: "Rebalance", accountId | modelId}
```

Attaching a model to an account is `wrapperDetail.assetAllocationId` at **create** time.
`AccountUpdate` carries no `wrapperDetail`, so **changing** a model on a live Seccl account is not
a field edit: it is `recurringOrders` for future money plus a `Rebalance` event for the existing
holdings. Confirm the intended change-model path with Seccl before building
`PUT /crm/accounts/{uid}/model` through to them — the current endpoint writes
`platform.accounts.model_id` locally and instructs nobody.

Our catalogue already models the versioned side properly: `platform.model_versions` +
`model_allocations` (a rebalance is one reviewable change, drafts until published) and
`platform.model_commentary`. `platform.models.products` is `product_type[]` and comes from
Seccl's `productType` list. The merge writes `model_id` on 311 of 374 Seccl accounts.

#### Morgan Lloyd

No model concept. ML holds a scheme and its policies; the investment strategy lives at the
custodian.

### 4.6 Illustrations

#### CTC — built

```
POST https://cam{test}.ctc.uk.com/Pages/Remote.aspx?wci=RunQuote&nodb=true   (text/xml)
POST /cam_illustration        → attach the resulting PDF URL to the ML contact/scheme
```

`app/integrations/ctc/xml_builder.py` emits and validates CTC Bespoke XML v1.6 (CTC1), 43 tests
against the vendor spec's own examples. `POST /crm/contacts/{uid}/illustrations` reads the
client's DOB and fund value off the record, calls the engine, and files the PDF as an
`illustration` document — so it inherits sharing, audit and client-portal visibility. Where CTC is
switched off or unreachable, a clearly-marked placeholder is filed in the same shape
(`ctc/placeholder.py`), because a half-finished illustration on a pension record is worse than an
obvious stand-in.

`cam_illustration` requires only `illustrationUrl`; `contactId` or `investorNumber` says who it
belongs to.

Two open items carried from `docs/morgan-lloyd/`: the first live CTC call must settle whether
`document_location` is a URL (if not the endpoint returns `stored: false` rather than filing a 404
body), and the `adviser_fees` / `annuity_basis` / tiered `product_fees` / `results` elements on
spec pages 19–29 are not yet fully mapped.

#### Seccl

```
POST /illustration/product      → GIA/ISA/Joint GIA, pension accumulation, pension drawdown, SMPI
POST /illustration/trade        → a trade illustration
POST /illustration/smpi
GET  /illustration/product/{firm}/?id=…&status=Pending
POST /files/generate  →  POST /files/illustration        (generate then download the PDF)
```

Set `calculating: "ASYNCHRONOUS"` and poll. The body is substantial but every field maps to
something we hold or compute: `adviserInfo.{adviserName, FCANumber, company}`
(**`FCANumber` is not held** — no FCA reference on a staff member or a tenant), `clientInfo`
(either an existing `id` or the full identity block — which is the create-a-client-through-an-
illustration path), `fees.{adviserCharge, discretionaryManagementCharge, initialAdviserCharge}`
(`platform.fee_packages.rule`), `payments.{initialPayment, singlePayments[], regularPayments[]}`,
`withdrawals.{singleWithdrawals[], regularWithdrawals[]}`, `modelId`, `retirementAge`
(`crm.contacts.retirement_age`), `schemeProductId` (`platform.product_platforms.account_open_reference`, 0181).

Worth noting for the roadmap: **Seccl's illustration engine is the only one of the three that can
illustrate a GIA or an ISA.** CTC does pensions (`SIPPEN`) and `routers/illustrations.py` refuses
anything that is not a pension wrapper, correctly — an illustration of a GIA through CTC is
meaningless. If firms want non-pension illustrations, Seccl is the route.

#### SEI

No illustration service in the 265 operations.

### 4.7 Transfers

What follows is the calls the adapters make, and where each field comes from. The catalogues
under each provider list what the vendor publishes; a path that is not in this section is not
sent.

Raising always writes Graphene's own row first (`POST /transfers` → `create_transfer`), then
runs `transaction.transfer`. That action is what instructs the provider. The local row is the
retry: a refusal stays `requested` with `provider_error`, and the detail action runs the
same action again. A success
moves the row to `submitted` and stores the provider's id. An accept whose body has no id is
not that success: the row is held at `manual_intervention` (`seccl_placed` or `sei_accepted`),
the detail shows why, and neither a second place nor a poll is offered. Later Seccl and SEI
progress is applied from the transaction sync when the workbench is read, matched on that id.
Morgan Lloyd's conformed transfers are not merged onto `platform.transfers`.

Which provider is called is the account's route. Custodian Seccl is Seccl, including a Seccl
SIPP. Custodian SEI on a pension whose scheme administrator is Morgan Lloyd is `ml_sei`.
Custodian SEI otherwise is SEI alone.

**The local row, before any provider call.** `platform.transfers` plus a
`platform.transaction_expectations` row (`kind` `transfer_in`, `status` `expected`).

| Column                                                                                  | Where it comes from                                                                                                                                                                                                                                                                                   |
| --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `uid`                                                                                   | Minted here. It is the `externalReference` every provider call carries, so a retry can be recognised.                                                                                                                                                                                                 |
| `account_id`, `contact_id`, `currency`                                                  | The account the drawer was opened on. `transfer_type` is derived from that account's product (`SIPP`/`PENSION`/`SSAS`/`TPPENSION` → `pension`, `ISA`/`JISA` → `isa`, `GIA` → `gia`, anything else → `cash`). The drawer does not ask.                                                                 |
| `ceding_provider`, `policy_number`                                                      | The drawer: the ceding scheme's name, and its policy or plan number.                                                                                                                                                                                                                                  |
| `basis`                                                                                 | The drawer. Cash or in-specie, never defaulted. Cash means the ceding scheme sells.                                                                                                                                                                                                                   |
| `extent`                                                                                | The drawer, except a Seccl account, which is stored `full` without asking.                                                                                                                                                                                                                            |
| `total_value`                                                                           | The drawer. The expectation's `amount` is the same figure.                                                                                                                                                                                                                                            |
| `crystallisation`, `crystallised_value`, `uncrystallised_value`, `safeguarded_benefits` | A pension. Stored `uncrystallised` / `partial` / `crystallised` (the drawer labels those No Drawdown, Partial Crystallisation, Full Crystallisation). The uncrystallised remainder is derived so the three figures agree. Unchecked safeguarded rights are stored false.                              |
| `pension_type`                                                                          | An SEI SIPP only. Morgan Lloyd's `PensionType` integer, 1–20.                                                                                                                                                                                                                                         |
| `data.provider_address_*`, `data.fca_reference`                                         | Address: a Seccl account. It starts as the client's and is replaced only when the switch is turned off. FCA reference: every pension, at the bottom of the drawer, once safeguarded rights are set. Seccl refuses the place without it. Account details stores the same pair on the account transfer. |
| `expected_date`                                                                         | Accepted by the API. The drawer does not ask it, so it is null on a transfer raised from the account.                                                                                                                                                                                                 |
| `note`                                                                                  | Not collected by the drawer. A note added later stays on the row. It is sent only to Morgan Lloyd, and only when it is already set.                                                                                                                                                                   |
| `status`                                                                                | `requested` until the provider accepts the instruction.                                                                                                                                                                                                                                               |

#### Creating a transfer

**Seccl** (`route` `seccl`) — one group, then a read to learn the transaction id.

`POST /portfoliotransactiongroup`, then `GET /portfoliotransaction/{firm}?linkId=…`.

The group and the transaction member both carry `firmId` (the firm's Seccl firm id, from the
client configuration) and `accountId` (`platform.links`, provider `seccl`, `link_type`
`primary`). The member is one `Product Transfer`, `movementType` `In`.

| Seccl field                                                | Source                                                                                                                                                                                                                                                     |
| ---------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `transactionSubType`                                       | `basis`: `cash` → `Cash`, otherwise `In Specie`.                                                                                                                                                                                                           |
| `previousProvider`                                         | `platform.transfer_providers.masterparty_id` for the ceding provider's name. Refused when missing.                                                                                                                                                         |
| `previousProviderRef`                                      | `policy_number`, or an empty string when the drawer left it blank.                                                                                                                                                                                         |
| `transferValue`                                            | `total_value`.                                                                                                                                                                                                                                             |
| `externalReference`                                        | `uid`. The group create is not idempotent.                                                                                                                                                                                                                 |
| `transferAccountAddress.address1` / `postCode` / `country` | `data.provider_address_line1`, `provider_postcode`, `provider_country`. All three are required.                                                                                                                                                            |
| `transferAccountAddress.address2` / `address3`             | `data.provider_address_line2` and `provider_city`, sent only when set.                                                                                                                                                                                     |
| `productDrawdownStatus`                                    | A pension only. `uncrystallised` → `No Drawdown`, `crystallised` → `Partial Drawdown`, `partial` → `Full Drawdown`. That is Seccl's own mapping: No Drawdown is the uncrystallised portion, Partial Drawdown the crystallised portion, Full Drawdown both. |
| `isPensionTransfer`                                        | `safeguarded_benefits`. Not "this is a pension".                                                                                                                                                                                                           |
| `fcaReference`                                             | `data.fca_reference`, and only when safeguarded rights are set.                                                                                                                                                                                            |

The id stored on `seccl_transfer_id` is the Product Transfer transaction inside the group, which
is what cancellation addresses. The group's `linkId` is only how that transaction is found.

**SEI, not a Morgan Lloyd SIPP** (`route` `sei`). Cash and in-specie are different calls, both
at raise time.

Cash is the payment expectation below (`POST /currencyreceiptservices/createCurrencyReceipt`),
using the transfer description and treatment codes. In-specie is
`POST /accountcustodytransferservice/accountCustodyTransfer/transferIn`. The body is
`{"data": {"attributes": …}}`.

| SEI field                                             | Source                                                                                                                                                                                                                                                                                                                                                                                                       |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `platformUser`                                        | `platform.tenant_platforms.config.platform_user` for provider `sei`.                                                                                                                                                                                                                                                                                                                                         |
| `account.accountNumber`                               | `platform.links`, provider `sei`, `link_type` `primary`.                                                                                                                                                                                                                                                                                                                                                     |
| `externalAccount.externalAccountName`                 | `ceding_provider`.                                                                                                                                                                                                                                                                                                                                                                                           |
| `externalAccount.externalAccountNumber`               | `policy_number`.                                                                                                                                                                                                                                                                                                                                                                                             |
| `externalAccount.externalAccountType`                 | `data.external_account_type`. The Graphene wrapper (`pension`, `isa`, `gia`) is not sent in its place.                                                                                                                                                                                                                                                                                                       |
| `primarySigningAuthority.primarySigningAuthorityName` | `data.signing_authority_name`, else the client's name.                                                                                                                                                                                                                                                                                                                                                       |
| `primarySigningAuthority.externalPrimaryTaxInfo`      | The tax number the ceding scheme holds for the person signing. SEI describes it as an SSN or tax number at the external system. For a UK resident that is the National Insurance number: `crm.contacts.national_identifier` where `national_identifier_type` is `nino` (or `national_insurance_number`). `data.signing_authority_tax_id` overrides it when the ceding scheme's copy was recorded separately. |
| `transferType.transferTypeId`                         | `basis`: `cash` → `1` (encash), otherwise `2` (in specie).                                                                                                                                                                                                                                                                                                                                                   |
| `assetTransferInstruction.assetTransferInstructionId` | `extent`: `full` → `1`, otherwise `2`.                                                                                                                                                                                                                                                                                                                                                                       |
| `assetDetails[]`                                      | Not sent. A transfer-in is accepted without asset lines, full or partial. The raise does not collect holdings.                                                                                                                                                                                                                                                                                               |
| `documents[].documentReferenceId`                     | `data.documents`. This call does not upload the forms. A transfer-in with none is refused.                                                                                                                                                                                                                                                                                                                   |
| `counterParty.counterpartyId`                         | `ref.transfer_providers.provider_id`. The ceding party. `kind = 'transfer_agent'` is Reference `TRANSFER_AGENT_ID`; custodians, cash agents and tax-wrap providers are the other kinds. The raise does not send this yet, and SEI refuses the call until a counterparty id (or an identifier type and value) is present.                                                                                     |

The returned `transferActivityId` (or `activityId`) is stored on `sei_activity_id`. An SEI
SIPP does not take this call: Morgan Lloyd has already asked the ceding scheme.

**Morgan Lloyd, an SEI SIPP** (`route` `ml_sei`) — `POST cam_createtransfer` at raise time.
SEI is not called yet.

| ML field                                                                                                         | Source                                                                                                                                                                          |
| ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `schemeReference`                                                                                                | `platform.links`, provider `morgan-lloyd`, entity `account`, `link_type` `primary`. That is the scheme, not the policy link.                                                    |
| `cam_policyno`                                                                                                   | `policy_number`. Required.                                                                                                                                                      |
| `cam_providername`                                                                                               | `ceding_provider`.                                                                                                                                                              |
| `cam_assettype`                                                                                                  | `1` (Pension Plan). Fixed.                                                                                                                                                      |
| `cam_status`                                                                                                     | `4` (Transferring). The record's status, not the six-value progress set.                                                                                                        |
| `cam_uncrystalllisedtransfervalue`                                                                               | `uncrystallised_value`. Three Ls, verbatim.                                                                                                                                     |
| `cam_crystallisedtransfervalue`                                                                                  | `crystallised_value`.                                                                                                                                                           |
| `cam_nonprtv`                                                                                                    | `total_value`. The whole transfer, despite the name.                                                                                                                            |
| `cam_typeoftransfer`                                                                                             | `basis`: `cash` → `0`, `in_specie` → `1`.                                                                                                                                       |
| `cam_transfertype`                                                                                               | `extent`: `full` → `809130000`, `partial` → `809130001`.                                                                                                                        |
| `cam_pensiontype`                                                                                                | `pension_type` (the column, else `data.pension_type`), sent only when set. Required by the vendor for a pension policy.                                                         |
| `cam_crystallised`                                                                                               | `partial` → `1`. `crystallised`, `uncrystallised` or `full` → `0`. If crystallisation was not stored, both amounts positive → `1`, otherwise `0` when either amount is present. |
| `cam_provideraddressline1` / `line2` / `line3`, `cam_providercity`, `cam_providercounty`, `cam_providerpostcode` | The same `data.provider_address_*` keys, sent only when set. The SEI SIPP drawer does not ask for an address, so these are omitted on a transfer raised from the account.       |
| `transferNotes`                                                                                                  | `note`, and only when the row already has one.                                                                                                                                  |

The returned `cam_personalpolicyid` GUID is `ml_transfer_id`.

#### Cancelling

**Seccl.** `PUT /portfoliotransaction/action/requestCancellation/{firmId}/{id}` with an empty
body. `id` is `seccl_transfer_id`, or, for a transfer that arrived on the feed,
`platform.transactions.source_transaction_id`. Seccl accepts it only while `transferStatus` is
With Previous Provider, Awaiting Documentation, Reviewing Documentation or Verifying
Documentation. A stock migration has no `transferStatus`, so it is not offered. A 200 is a
request: local `cancelled` waits for a later read, and Pending Cancellation stays `submitted`
and is shown as Cancellation requested.

**Morgan Lloyd.** There is no cancel action. The adapter reads the vendor's current progress
first (`POST cam_getpensiontransfer`, body `clientReference` = the client's Morgan Lloyd
contact GUID from `platform.links`, and `policyNo` = `policy_number`), and refuses once that
progress is Funds received or later — the money has already moved. Otherwise
`PATCH cam_personalpolicies({ml_transfer_id})` with `cam_transferstatus` `5` (No longer
transferring). That is the progress set, not `cam_status`. The local row is then `cancelled`.

**SEI.** No cancel call. A transfer SEI is chasing is not withdrawn from here.

#### Generate the custodian's payment expectation

The detail action **Generate ACR with custodian** runs `transaction.transfer`. It is an anticipated
cash receipt: SEI expects the money and does not collect it. There is no `receiptType` of ACR.
The same call is what a SEI-only cash transfer makes at raise time. On an SEI SIPP it is
offered only once Morgan Lloyd has completed the transfer (`status` `funds_sent`, cash, and
the expectation not already `instructed`, `matched` or `reconciled`), and that press is a
second run of the same action. It is not sent while Morgan Lloyd is still holding the cash.

`POST /currencyreceiptservices/createCurrencyReceipt`.

| SEI field                                 | Source                                                                                                           |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `accountNumber`                           | `platform.links`, provider `sei`, `link_type` `primary`.                                                         |
| `portfolioId`                             | `platform.links`, provider `sei`, `link_type` `portfolio`.                                                       |
| `firmId`                                  | `platform.tenant_platforms.config.firm_id`.                                                                      |
| `receiptInstructions.currencyCode`        | `currency`, or `GBP`.                                                                                            |
| `receiptInstructions.receiptMethod`       | `5` (BACS). How the money is expected to arrive. No `payerInfo`, so nothing is collected.                        |
| `receiptInstructions.descriptionTypeCode` | `config.transfer_description_code`. Not the contribution description code.                                       |
| `receiptInstructions.treatmentTypeCode`   | `config.transfer_treatment_code`. Not the contribution treatment code.                                           |
| `receiptInstructions.receiptAmount`       | The expectation's `amount`, else `total_value`.                                                                  |
| `receiptInstructions.receiptAmountType`   | `1` (gross).                                                                                                     |
| `receiptInstructions.externalReference`   | `uid`.                                                                                                           |
| `processingScheduleDetails.once.dueDate`  | The expectation's `due_date`, else `expected_date`. Omitted when neither is set. SEI rejects a date in the past. |

`activityId` is stored on the expectation as `provider_reference` (status `instructed`) and on
the transfer as `sei_activity_id`.

An in-specie SEI SIPP does not use this call. Once Morgan Lloyd has sent the assets the detail
action **Register free receipt** is that same `transaction.transfer` run:
`POST /transfersfreereceiptservice/freereceipt`, with
`platforUser` (SEI's spelling), `portfolio.{accountNumber, portfolioId, firmId}` from the same
links and `firm_id`, and `receiptAsset[]` from `data.assets` when the row already has them
(`identifier_type`, `identifier`, `issue_country`, `units`). The raise does not collect
holdings, and a transfer-in does not send them.

#### Seccl — endpoint known, and the fullest support of the three

```
GET  /masterparty/platform/                                → available ceding providers
GET  /masterparty/platform/?supportsPayUk=True             → Pay.UK-capable ceding providers
GET  /masterparty/transferoutprovider/?productTransferType=Cash
POST /portfoliotransactiongroup                            → transfer in / out / partial
PUT  /portfoliotransaction/action/bookvalue/{firm}/{id}     → book cost, one
POST /upload/bookvalue/{firm}                              → book cost, bulk
PUT  /portfoliotransaction/action/requestCancellation/{firm}/{id}
PUT  /portfoliotransaction/action/firmTransferHistoryUpdate/{firm}/{id}   → add an update event
GET  /portfoliotransaction/producttransfer/{firm}          → the transfers workbench
POST /filestore/manualtransferin  →  S3 PUT  →  POST /portfoliotransactiongroup   (manual, with a form)
```

A transfer is a `Product Transfer` transaction (`transactionSubType: Cash | In Specie`) carrying
`previousProvider` (a masterparty id), `previousProviderRef`, `transferAccountAddress`,
`transferValue`, and for a pension `isPensionTransfer` + `productDrawdownStatus`. A transfer
**out** is two transactions in one group: the `Product Transfer` out plus a `Payment` /
`Transfer` out naming `receivingProviderId`.

| Seccl field                                | Our source                                                                                                                                                                                                                                                               |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `previousProvider` / `receivingProviderId` | `platform.transfer_providers.masterparty_id` (0042/0046). Place refuses when it is missing.                                                                                                                                                                              |
| `previousProviderRef`                      | `platform.transfers.policy_number`. On a transfer read off the feed the same value is `data.previous_provider_ref`, and that is the detected row's policy number. Stock migrations do not carry one.                                                                     |
| `transferValue`                            | `platform.transfers.total_value`                                                                                                                                                                                                                                         |
| `externalReference`                        | `platform.transfers.uid` — the group create is not idempotent, so a duplicate can be recognised                                                                                                                                                                          |
| `isPensionTransfer`                        | Safeguarded rights (`safeguarded_benefits`), **not** "this is a pension". Pension Schemes Act 2015 s.48(8).                                                                                                                                                              |
| `productDrawdownStatus`                    | Crystallisation: uncrystallised → No Drawdown, crystallised → Partial Drawdown, partial → Full Drawdown                                                                                                                                                                  |
| `fcaReference`                             | `platform.transfers.data.fca_reference`, required when safeguarded rights are set                                                                                                                                                                                        |
| `transferAccountAddress`                   | `platform.transfers.data` (`provider_address_line1`, `provider_postcode`, `provider_country`, and the optional line 2 and city). On a Seccl account the raise drawer starts from the client's address and asks for another only when the ceding scheme's record differs. |

Seccl's `transferStatus` is stored on `ceding_status` and mapped by `seccl_stage`: With Previous
Provider / Pending / Placed → `submitted`; Onboarding Assets and the documentation and sell-down
statuses → `in_progress`; Pending Cancellation stays `submitted` and is shown as Cancellation
requested, because a cancellation request is not the transfer moving on to the ceding scheme;
Awaiting Crystallisation Information → `manual_intervention`; Completed
→ `funds_sent` (shown as Completed); Rejected / Deleted / Cancelled → `cancelled`. A feed row
uses the same map (`synced_stage`). A stock migration has no transfer status, so a completed
transaction is `funds_sent` and an open one is `submitted`. Seccl Completed is the custodian
booking the movement. Graphene `completed` is the reconciliation.
`firmTransferHistoryUpdate` appends a chasing event whose only code is "Client action complete"
— it is not a standing note, so adviser notes stay on `platform.transfers.note` or
`platform.transactions.adviser_note`.
The status flow ends at Completed (the custodian). Reconciled `completed` is not drawn on it.
`requestCancellation` is a request, so local `cancelled` waits for a later read. While Seccl
says Pending Cancellation the flow highlights Cancellation requested at the stage the transfer
had reached. The transaction
id lives on `platform.transfers.seccl_transfer_id` (0188). A transfer that arrived on the feed
uses the same call, addressed by `source_transaction_id`, and only while Seccl's
`transferStatus` is still one it will cancel (with the previous provider, or a documentation
check). A stock migration has no `transferStatus`. Seccl does not send a received amount
separate from `transferValue`.

#### Morgan Lloyd — endpoint known

```
POST  /cam_createtransfer                → the transfer
POST  /cam_getpensiontransfer            → read one
POST  /cam_getalltransfersbyscheme       → read all for a scheme
GET   /cam_personalpolicies              → read by policy number
PATCH /cam_personalpolicies({id})        → update date / amount / notes
POST  /cam_HubwiseTransfer               (UNDOCUMENTED)
```

`cam_createtransfer` is what an SEI SIPP sends at raise time. The required fields and where
they come from are in the table above; `basis`, `extent` and `pension_type` are columns.

| ML field                           | Our source                                                                                           |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `cam_policyno`                     | `platform.transfers.policy_number`                                                                   |
| `schemeReference`                  | `platform.links` (provider `morgan-lloyd`, entity `account`, `link_type` `primary`)                  |
| `cam_providername`                 | `platform.transfers.ceding_provider`                                                                 |
| `cam_assettype`                    | `1` Pension Plan (only allowed value)                                                                |
| `cam_status`                       | `4` Transferring (only allowed value)                                                                |
| `cam_uncrystalllisedtransfervalue` | `platform.transfers.uncrystallised_value`                                                            |
| `cam_crystallisedtransfervalue`    | `platform.transfers.crystallised_value`                                                              |
| `cam_nonprtv`                      | `platform.transfers.total_value` — the name says "non-protected" and it means the **whole** transfer |
| `cam_typeoftransfer`               | `basis`: `0` Cash / `1` In Specie. See the field table above.                                        |
| `cam_transfertype`                 | `extent`: `809130000` Full / `809130001` Partial. A column since 0148.                               |
| `cam_pensiontype`                  | `pension_type`, the vendor's 1–20 set. A column since 0176, asked of an SEI SIPP.                    |

ML's own status vocabulary is six values (`morgan_lloyd.enums.TransferStatus`): awaiting forms /
ML to request / awaiting ceding scheme / funds received / funds sent / no longer transferring.
0056 added exactly one status to ours, `funds_sent`, because "the ceding scheme paying ML" and "ML
paying the custodian" are different weeks of chasing. Our lifecycle stays authoritative and is
monotonic — which is why ML's `transfers` relation reaches the lake but deliberately does **not**
merge into the book.

#### SEI — available; the client wraps it

The spec documents a complete custody-transfer surface:
`POST /accountcustodytransferservice/accountCustodyTransfer/transferIn`,
`…transferOutservice/…/transferOut`, `POST /transfersfreereceiptservice/freereceipt` (+ `async`,

- `PATCH /{activityId}`, + `GET /freereceiptstatus`), the free-delivery mirror,
  `POST /pendingtransfersstatusservice/getPendingTransfersStatus`,
  `PATCH /transferscustodytransferservice/custodytransfer/{activityId}`,
  `PATCH /transferssettlementservice/settlementservice`, and the ACAT control-number reads.

`TransfersApi` wraps the client-facing pair, both free-movement legs with their status reads,
the pending-transfers read and the in-flight amendment. The transfer-in body (69 leaves;
transfer-out 106, free receipt 156) is well shaped —
`transferType.transferTypeId` (1 Free Receipt-Encash, 2 Free Receipt-In Specie),
`assetTransferInstruction` (1 Full / 2 Partial / 3 Full Liquidation / 4 Partial Liquidation),
`externalAccount.{externalAccountName, externalAccountNumber, externalAccountType}`,
`primarySigningAuthority`, `counterParty`, optional `assetDetails[]`, and
`documents[]` with a `documentTypeId` — so what remains is the journey around it, not the call.
two pieces of that journey are ours: the transfer forms have to be uploaded through the document
service first (a request with no forms is refused by the ceding provider rather than by SEI,
which is a much slower failure), and §6 #10's remaining gap is Seccl's ceding address — `basis` /
`extent` / `pension_type` are columns as of 0148 and 0173.

Which of those calls runs, and when, is the section above. SEI-only cash is the anticipated
receipt; SEI-only in-specie is `transferIn`. Asset lines are not required. Uploaded form
references are still required by this adapter (this does not upload the forms). An SEI SIPP does not call `transferIn`.
Its cash receipt waits until Morgan Lloyd has completed the transfer, and an in-specie free
receipt waits until the assets have been sent. The activity id is
`platform.transfers.sei_activity_id` (0188). Settled in-specie lines in the nightly drop are one
row per instrument; `RELATED_ACTIVITY_ID` on TransactionHeader is what groups them. The
transaction-details API does not return free receipts or security transfers, so that grouping
is read from the file rather than added to the API sync.

Pending-transfer codes drive the saga only for a direct SEI `transferIn` (`sei_stage`). Code 7
(`COMPLETE`) is `funds_sent` (shown as Completed), not Graphene `completed`. A currency receipt
and a Morgan Lloyd free receipt are not in that list — their activity ids are followed on the
transaction feed, and Refresh from SEI is not offered. A transfer completes at **our**
reconciliation, which is why no vendor map writes `completed`.

### 4.8 Fees

#### Seccl — read is built, write is not

```
GET   /v1/fee-structures/{firmId}                 → the firm's register  (the only /v1/ path in the sync)
GET   /v1/fee-structures/{firmId}/?type=Platform Fee
PATCH /client/{firm}/{id}    {feeStructures:{adviserFeeId}}
PATCH /account/{firm}/{id}   {feeStructures:{adviserFeeId}}
PATCH /portfoliogroup/{firm}/{id}                 → fee group's adviser fee structure
POST  /portfoliogroup                             → a client fee group
POST  /portfoliotransactiongroup                  → ad-hoc / initial adviser fee
POST  /portfoliotransaction                       → a platform fee posting
GET   /posting/cash/?transactionCode=PFOF&postDateFrom=…&postDateTo=…   → reconciliation
```

The register lands as `raw.fee_structures` → `platform.fee_packages` → the book via
`_sync_fee_packages` (0057's `UNIQUE(tenant_id, external_id)` is what the upsert needs; without
it a nightly run re-inserted the firm's charges every night). Three rules were forced by the
provider layer and are worth remembering when writing _back_: `fee_type` is **derived** (Seccl
publishes one `tiers` array for fixed/percentage/tiered, and a shape matching none of them
publishes NULL — a counted reject, never a guess that would misprice an invoice); rates come from
Seccl as a **fraction** (`0.0035` = 0.35%); and tier bands are normalised **contiguous** because
Seccl starts the next band at the next penny (`10000.01`), which `catalogue.validate_fee_rule`
rejects as a gap.

Assigning a fee package is `PUT /crm/accounts/{uid}/fee-package` on our side — writes
`platform.accounts.fee_package_id`, instructs nobody.

#### SEI — available; the client wraps it

A full fee administration surface exists, and `FeesApi` wraps it:
`POST /feepackageadmin/periodic/feePackageAdmin` (create/modify a package),
`POST /feeruleadmin/periodic/feeRuleAdmin` (rules),
`POST /feepackageassignmentaccount/feePackageAssignmentAccount`
(`accountId*` + `feePackageProfileId*` + `collectionPortfolioId` + `copyPackageFlag`),
`…assignmentgroup`, `…assignmentfinalfeedate/finalFeeDateAPI`,
`POST /createfeegroupservice/feegroup/create` + `addAccountToFeeGroup` +
`removeAccountFromFeeGroup`,
`POST /initiateonetimefeesadhocservice/initateOneTimeFeesAdhoc` (ad-hoc fee —
`fee.adhoc` sends this: direct charge `1`, the firm's one-time fee type, collection
date `MM/DD/YYYY`, taken from the account and not reversible),
`POST /maintainrealtimefeeservice/account-maintenance/realtimefee`,
`GET /retrievefeepackagedetails/fee-package-details/retrieve` and
`GET /periodicfeepackageruledetails/periodic/feePackageRuleDetails`.

**Read the drop rather than these, though.** The nightly feed already carries the whole register
— `raw.fee_package_rule` (rate, basis, rate type, VAT, qualifiers, `FEE_PACKAGE_PROFILE_ID`),
`raw.fee_package_usage` (per-account assignment, discounts, waivers, collection schedule),
`raw.fee_group` and `raw.fee_computation` — so nothing here should populate
`platform.fee_packages`. `feePackageProfileId` is the id that ties the two together and is the
value `platform.fee_packages.external_id` should hold for SEI; `getAccountDetailsinfo` returns it
too, for confirming an assignment we have just made.

#### Morgan Lloyd — no fee write, and the question of keeping it in step

ML has no fee endpoint. Its fee touchpoints are indirect: the adviser fee appears in the CTC
illustration (`adviser_fees`), in the scheme's own standing data at Clifton, and as a
`Periodic Fee` transaction in the custodian feed.

**Should we keep the ML client/account in step?** The three things ML holds that drift are the
contact's personal details, the scheme's review date, and the policy valuation. The first two have
write endpoints (`PATCH /contacts({id})` — `update_contact`, borrowing Create Contact's field
list; `POST /cam_updatescheme`) and both are classed `retry: safe` in
`app/platform_actions.py` because they set absolute values. Fees are not among them, so the honest
answer is: **no, fees cannot be kept in step with ML through the API**, and the fee figures a
client sees on an ML-produced document come from Clifton's own standing data. If a firm changes an
adviser charge in our catalogue, someone has to tell Clifton. That is an operational hand-off, and
it should be a task (`docs/tasks.md`) raised by the fee-package edit, not a silent divergence.

### 4.9 Daily valuations to Morgan Lloyd

```
POST /cam_createwrapper              → the policy, once   (prerequisite)
POST /cam_createpolicyvaluation      → one policy's valuation
POST /cam_createpolicyvaluationsfromcsv   → bulk (UNDOCUMENTED: {valuationDate, csvFile})
```

**`pkgs/morgan-lloyd/src/morgan_lloyd/endpoints/valuations.py` calls
`cam_updateschemevaluation`, which does not exist.** Its own docstring says so — "STUB … the CAM
action contract is not yet confirmed … placeholder action name" — and the org's `$metadata` has no
such action. The real call is `cam_createpolicyvaluation`, and the shape is fully documented in
`pkgs/morgan-lloyd/src/morgan_lloyd/vendor_manifest.json`:

`policyValuation` is a **JSON document passed as a single string**:

| Field                                   | Required | Our source                                                                                                                  |
| --------------------------------------- | -------- | --------------------------------------------------------------------------------------------------------------------------- |
| `PolicyReference`                       | yes      | **not held** — `platform.links` records the ML _contact_ and _scheme_; nothing records the policy `cam_createwrapper` mints |
| `PolicyValue`                           | yes      | `platform.valuations.market_value` (must equal the sum of the fund splits)                                                  |
| `ValuationDate`                         | yes      | `platform.valuations.as_of_date`                                                                                            |
| `FundSplits[].Name`                     | yes      | lake — `tenant_{uid}.positions`                                                                                             |
| `FundSplits[].Isin`                     | no       | `platform.instruments.isin`                                                                                                 |
| `FundSplits[].ValuationHolding` (units) | yes      | lake                                                                                                                        |
| `FundSplits[].Price`                    | yes      | lake                                                                                                                        |
| `FundSplits[].MarketValue`              | yes      | lake                                                                                                                        |
| `FundSplits[].PriceDate`                | yes      | lake                                                                                                                        |
| `provider` (the action's own field)     | yes      | `MORGAN_LLOYD_INVESTMENT_PROVIDER_ID`, falling back to `adviser_firm_id` — the vendor issues one GUID under three names     |

Two real obstacles, in order:

1. **No policy link.** `sync.py` records `contact` and `account` (scheme) links; the policy
   number from `cam_createwrapper` is recorded nowhere. Without it there is no
   `PolicyReference` to send. Fix: record it as a `platform.links` row —
   `entity_type='account'`, `provider='morgan_lloyd'`, `link_type='policy'` (the table's
   `link_type` exists for exactly this, and `UNIQUE(tenant_id, provider, entity_type, entity_id,
link_type)` after 0066 permits a second row per account).
2. **`platform.valuations` has no instrument breakdown.** It is account × date × source with
   `market_value`/`cash_value`/`book_cost`. `FundSplits` needs holdings, and holdings are
   deliberately lake-only (`positions` stays out of the merge; `docs/provider-pipeline.md`). So
   the valuation push has to read the lake — which means it belongs behind `crm._firm_read`'s
   sibling seam, not in a worker that would degrade to _nothing_ on an unreadable warehouse and
   file a valuation of zero. **Refusing is correct here**, the same posture the withdrawal gate
   takes.

`ML_VALUATIONS_PUSH_ENABLED` is the flag the stub reads. Leave it off until both are fixed.

Also note `cam_createpolicyvaluation` writes to `cam_policyvaluations`, one of the **85 `cam_*`
tables our service principal cannot read** (403). Writing through the action and reading back
through the entity set are different permissions; do not assume a successful write is verifiable.

---

## 5. SEI reference data — the drop, the API, and the workbooks

**The headline finding, and it reverses this document's first draft: the nightly SFTP drop is the
reference-data source, not the static-reference workbooks.** The drop's `Reference` report is one
long TYPE-discriminated table carrying **183 code sets** (29,019 rows in the p04 corpus of
2025-10-28), and between it and the other 46 reports it answers almost every id an enrolment,
a cash instruction or a model assignment needs. The workbooks are a point-in-time snapshot of a
subset of the same thing, and each one is demonstrably stale against its own environment.

**Only i31 (test) and p04 (production) are in use.** i03/i04/i10 were earlier test environments,
are configured nowhere, and their workbooks survive as _evidence about the shape_ of SEI's
reference data — not as a source. `services/sei/.env` is set to `SEI_ENV=i31` with the i31 API
base URL; `.env.example` now says all of this at the top.

### 5.1 What the drop already answers

Read this table before reaching for an API call. Every row is a field the enrolment or cash
payload needs, and a place in the nightly drop that already holds it.

| Field the vendor asks for                                         | Where it is in the drop                                                                        |
| ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `accountTypeId`                                                   | Reference `ACCT_TYPE`                                                                          |
| `accountSubTypeId`                                                | Reference `ACCOUNT_SUB_TYPE` (8 codes)                                                         |
| `accountClassificationId`                                         | Reference `ACCOUNT_CLASSIFICATION` — **per firm**, and it includes Individual                  |
| `accountSubClassificationId`                                      | Reference `ACCOUNT_SUB_CLASSIFICATION` (162 rows)                                              |
| `firmInvestmentCapacityId`                                        | Reference `FIRM_INVESTMENT_CAPACITY` — 3 Discretionary / 4 Advisory / 5 Execution Only         |
| `clientTypeId`                                                    | Reference `CLIENT_TYPE` — 5 Individual / 6 Organization                                        |
| `titleId` / `suffixId`                                            | Reference `TITLE` (61) / `SUFFIX` (14)                                                         |
| `maritalStatusId`                                                 | Reference `MARITAL_STATUS` (7)                                                                 |
| `genderId`                                                        | Reference `GENDER` — 1 Male / 2 Female                                                         |
| `certifiedFATCAComplianceId`                                      | Reference `FATCA_COMPLIANCE` (11)                                                              |
| `taxIdTypeId`                                                     | Reference `TAX_ID_TYPE` — 7 is National Insurance Number                                       |
| `phoneMethodId`                                                   | Reference `PHONE_METHOD` (4)                                                                   |
| `reinvestmentMethodOfFundsId`                                     | Reference `MUTUAL_FUND_REINVEST_PREF` (13)                                                     |
| `typeOfTrustId`                                                   | Reference `TYPE_OF_TRUST` (23)                                                                 |
| `taxWrapProviderId`                                               | Reference `TAX_WRAP_PROVIDER` (3)                                                              |
| `accountQualifierId`                                              | Reference `ACCOUNT_QUALIFIER` (37, per firm)                                                   |
| `organizationTypeId` / sub-type                                   | Reference `ORGANIZATION_TYPE` (15) / `ORGANIZATION_SUBTYPE` (14)                               |
| `inheritanceDesignationId`                                        | Reference `INHERITED_DESIGNATION` (4)                                                          |
| `apsId` on a model assignment                                     | Reference `ALLOWABLE_PROCESSING_SCHEDULE` (17), and `ModelsIntExt.ALLOWABLE_PROCESSING_SCH_ID` |
| `descriptionTypeCode` / `treatmentTypeCode`                       | Reference `DESCRIPTION_CODE_ID` (6,108, per firm) / `TREATMENT_CODE` (758)                     |
| the DSR `servicingRelationshipId` + team/employee                 | Reference `DSR` (474) + `DSR_TYPE_NAME` (25), and `ClientAccountLink`'s 80 slot columns        |
| `employeeEntityId` for an adviser                                 | `UserDetail.ENTITY_ID` + `UserTeamRoleDetail`                                                  |
| **`portfolioId`**                                                 | **`Accounts.PORTFOLIO_ID`** — the report is grained account × portfolio                        |
| `payToProfileId` on a disbursement                                | **`PaidTo.PROFILE_ID`**, copied onto `platform.accounts.bank_accounts.provider_ref`            |
| `feePackageProfileId`                                             | **`FeePackageUsage` / `FeePackageRule`.FEE_PACKAGE_PROFILE_ID**                                |
| `modelId` on a model assignment                                   | `ModelsIntExt.MODEL_ID` with `MODEL_TYPE` and `MODEL_STATUS`                                   |
| a standing instruction's whole shape                              | **`RecCashActivities`** — 55 columns (see below)                                               |
| `STMT_PACKAGE_ID`                                                 | `StatementPackage`                                                                             |
| the transaction-type vocabulary                                   | Reference `TRANSACTION_TYPE` (124), already used by `platform.transactions`                    |
| the recurrence vocabulary                                         | Reference `RECURRENCE_FREQUENCY` (3) + the `*_RECURRENCE_*` sets                               |
| `AMOUNT_SPECIFICATION_TYPE`, `RAISE_CASH_METHOD`, `PAY_TO_METHOD` | Reference, all three                                                                           |

Three of those are worth calling out because they contradict claims this document made in its
first draft, and because each closes a gap that looked structural:

**`portfolioId` is in the drop.** `app/integrations/sei/cash_receipts.py` says it "has no source"
and "arrives from SEI onboarding". It does not: the nightly `Accounts` report is grained **account
× portfolio** and carries `PORTFOLIO_ID`, `PORTFOLIO_TYPE` (189 Capital / 190 Income),
`PORTFOLIO_NAME`, `PORTFOLIO_PURPOSE` and `LINKED_PORTFOLIO_ID`. That also means the "no portfolio
entity" gap has a real source rather than needing to be invented.

**`RecCashActivities` is the standing-instruction feed.** 55 columns: `RECURRENCE_FREQUENCY` with
the daily/weekly/monthly interval sets, `RECURRANCE_SCHEDULE_START_DT`, `RECURRENCE_SCHEDULE_END_DT`,
`RECURRENCE_END_AFTER_X_OCCURRENCES`, `PAYER_PROFILE_ID`, `PAY_TO_PROFILE_ID`, `NET_AMOUNT`,
`GROSS_AMOUNT`, `PAY_TO_METHOD`, `NEXT_ACTIVITY_START_DATE`, `NEXT_ACTIVITY_ID`,
`PROCESSING_SCHEDULE_ID`, `DESCRIPTION_CODE_ID`, `TREATMENT_CODE`, and the invest-cash /
raise-cash options. `raw.rec_cash_activities` already lands it; **nothing conforms it**. So §6's
mandate gap is a modelling job over data we already receive, not an integration.

**`FIRM_INVESTMENT_CAPACITY` maps onto `crm.service_type` exactly** — 3 Discretionary, 4 Advisory,
5 Execution Only against `discretionary` / `advised` / `execution_only` after migration 0104.
There is no code map to invent.

### 5.2 What the drop cannot answer

Five things, and the first is the only one that blocks anything today.

**1. Enrolment templates — the whole enrolment path turns on this.** `clientTemplateId` and
`accountTemplateId` are REQUIRED by `POST /accountservice/accounts`, appear under no Reference
`TYPE`, and no report carries one. Two API reads fix it, and they are now wired:
`GET /enrollmentclienttemplates/crm/client/templates` and
`GET /enrollmentaccounttemplates/crm/account/templates` (lowercase — the spec's
`enrollmentAccountTemplates` has no proxy and 404s), via
`services/sei/models/api_templates.py` → **`ref.enrollment_templates`** (two requests per firm
per day, `strategy: full_merge` so a retired template stops appearing). §5.4 is the schema answer.

**2. The activity STATE vocabulary.** The drop has `ACTIVITY_STATUS` (15 codes) and _not_ the
~430-member state set, so `activityState: 22` is an integer with no meaning.
`ReferenceApi.system_codes` is the lookup; nothing reads it yet. Single activities come back with
`activityStateDescription` populated, so this only bites on a listing.

**3. Cash activity milestones** — the _why_ behind a state (process 102 Cash Disbursement alone
has ~35, from "Start Disbursement" through "OFAC/AML Check of Pay To and Pay For parties" to
"Set to Complete"). In the **i03** workbook only, 107 rows. This is the sheet you actually want
when a disbursement is stuck.

**4. `STMT_BRANDING_ID`.** No Reference type, no report, no endpoint. The i04 workbook's one row
(31021 Blackfinch Branding) is the only record. Only matters if we ever drive SEI statement
production.

**5. `toleranceMultiplier` and `eventProcessingId`.** The multiplier is not in the drop.
The assignment read (`raw.api_models`) publishes the one already on a portfolio that uses
the model, and `model.attach` sends that. A model with no published multiplier is not sent.
`eventProcessingId` is still not stored per firm. A first assignment requires it, so the
call sends `3` (As per Rule).

### 5.3 The workbooks, and why they are evidence rather than a source

`i04_Static_Reference_Data.xlsx` has ten sheets (firm **3102**, Blackfinch): Account Types (10
rows), Account Sub Types (8), Enrollment Template (3), Firm Acct Classification (54), DSR Tags
(1,210), Model ID (10), Package ID (6), Branding ID (1), Activity Status (15), Activity State
(431). i03 adds `Cash Activity Milestones` (107) and has 6 enrolment templates; i10 has five
sheets. Every sheet except Activity State, Cash Activity Milestones and Branding ID is superseded
by the drop.

The reason to distrust them is not age in the abstract — it is that they are **provably
incomplete against a request that succeeded**. `secno-6-apirequest.json` in the same folder is a
real i31-family enrolment against firm 3102 (a Blackfinch AIM ISA for one individual), and it
uses two values no i04 sheet contains:

```
firmId                   3102
caseReference            B179F899-…            (our reference)
externalPartyId          B079F899-…            (our client reference)
clientTypeId             5                     Individual
clientTemplateId         2981049               Blackfinch Client            ← Enrollment Template
certifiedFATCAComplianceId 2
taxIdTypeId              7 + taxIdNumber "JM665532A"     (a NINO)

  client-level DSR
  employeeServicingRelationshipList  {59266, employeeEntityId 57821597}    BDM
  teamServicingRelationshipList      {59263, teamId 704265}               Product Team (AIM Product)

accountTypeId            172                   Stocks and Shares ISA
accountSubTypeId         1                     Investment Account
accountClassificationId  48518                 Individual   ← IN NO WORKBOOK; in Reference
firmInvestmentCapacityId 3                     Discretionary
accountTemplateId        2981141               Blackfinch AIM ISA - SITest ← in i03 ONLY
reinvestmentMethodOfFundsId 2                  Cash
accountBaseCurrency      GBP

  account-level DSR
  employeeServicingRelationshipList  {59267, 57821597}   BDM
                                     {59268, 57820278}   Adviser
                                     {59269, 57817525}   Investment Decision Maker
  teamServicingRelationshipList      {59253, teamId 703609}   Fee Receiving IFA Firm
                                     {59254, teamId 703609}   IFA Firm
                                     {59264, teamId 704265}   Product Team

accountOwner.primaryOwnerExternalId  = the same externalPartyId
portfolios[0].principalPortfolio.portfolioName  "Thrive ISA"
```

`accountClassificationId 48518` is absent from the Firm Acct Classification sheet, which lists
only classes that HAVE sub-classifications — Individual has none. The Reference report's
`ACCOUNT_CLASSIFICATION` carries all nine per firm (48518–48526 for 3102, 48527–48535 for 3106,
48555–48563 for 3108) including Individual and House Account. `accountTemplateId 2981141` is in
i03 as "Blackfinch AIM ISA - SITest" and in no i04 sheet at all.

Three things the sample settles that no field list does:

- **The DSR composition is firm policy, not a per-client choice** — six grants on the account,
  two on the client. IFA Firm and Fee Receiving IFA Firm are the same team; Product Team is the
  same for every AIM ISA; only Adviser varies per client. `TAG_ID` is the API's
  `servicingRelationshipId`, and the client/account split is why one role has two ids (59266 BDM
  at client level, 59267 at account level).
- **Use `crm.contacts.uid` as `externalPartyId`.** The sample uses a UUID; a 12-character nanoid
  is well inside SEI's field, is already the public identifier our URLs carry, and makes the SEI
  record self-describing without a link row for the _first_ call. `caseReference` should be the
  `crm.applications.uid`.
- **The `Model ID` sheet's type-8 models decide which assignment variant applies.** Reference
  `MODEL_TYPE` gives the vocabulary: 8 Investment Model, 4 Implementation Model, 3 Asset
  Allocation Model, 5 Strategy. All three of the firm's real models (83011 AIM Growth, 83012 AIM
  Income, 83013 AIM Mixed) are type **8**, so `assignment/investment` is the variant this firm
  needs — see §4.5.

### 5.4 Where the enrolment templates live — the schema answer

The question this raised is a good one: templates are firm-configured, so do `platform.products`
need extending, and is it a `platform.links` job?

**The client template is firm-level and needs no schema change.** One value per firm, and
`platform.tenant_platforms.config` is already where the firm's SEI ids live (`firm_id`, the two
contribution codes). Migration `0116` adds a `COMMENT` naming the full set so a second convention
does not grow: `firm_id`, `client_template_id`, `account_classification_id`,
`firm_investment_capacity_id`, the DSR grant composition, and the contribution
description/treatment codes.

**The account template is per shelf PRODUCT and per platform**, which is exactly the grain
`platform.product_platforms` already has — it joins one `platform.products` row to one
`platform.platforms` row and carries `external_id` / `external_name` / `external_type` / `data`.

Blackfinch's two account templates are "Blackfinch AIM ISA" (2981050) and "Blackfinch AIM IHT"
(2981051), and they map to two different wrappers (ISA and GIA — an AIM IHT account is a GIA
holding AIM shares for business relief), so `platform.products`' key of
`UNIQUE (tenant_id, product_type)` (0044) represents both. **An earlier revision of this document
said both were "ISA/GIA at the wrapper level" and implied a collision; that was wrong** — the two
are distinct product types and nothing collapses.

The real constraint is one level along, and it is worth naming because it bounds this design:
0044's key means **one product per wrapper per firm**, and `product_platforms` is unique on
`(product_id, provider)` (0143), so there is room for exactly **one opening reference per wrapper per
custodian**. A firm offering two different ISA propositions at one custodian — two ISA enrolment
templates — cannot be represented. That is 0044's deliberate decision ("a product is one thing
however many custodians hold it") and the right default; if a firm needs the second, the shelf key
has to change first, not this column.

Migration **`0199_account_open_reference`** adds one column there (an earlier draft of this
document numbered it 0116; that revision was spent on `comms_messages`):

```sql
ALTER TABLE platform.product_platforms
  ADD COLUMN IF NOT EXISTS account_open_reference text;
```

Named for what it is _for_ rather than for SEI, because **Seccl asks the same question in
different words**: `wrapperDetail.schemeProductId`, required to open a PENSION account, is also a
per-product provider reference and was also unheld (§4.1 listed it as a gap). One column closes
both, and the third custodian that asks will not need a third.

Why not the columns that were already there:

- **`external_id` is taken.** It holds the platform's reference for the product as the pipeline
  READS it (Seccl's wrapper type, SEI's `ACCT_TYPE`). The open reference is what a write SENDS.
  Different values, different lifetimes.
- **`data` jsonb would work and is wrong.** An account cannot be opened without this, so it is a
  documented part of the contract rather than provider miscellanea. A key in a blob has no
  comment, no constraint, and nothing tells you it is missing until the vendor refuses.
- **`platform.links` is for an entity's identity at a provider**, keyed by `entity_id`. A shelf
  product's opening template is configuration on a (product, platform) pair — the row
  `product_platforms` already has.

Nullable, no default, no backfill: there is no value to guess, and an open attempt must refuse
naming the column. SEI answers a missing template by naming the template, not by saying none was
supplied — so the local refusal is more informative than the vendor's.

**Discovered, then attached.** `ref.enrollment_templates` lists what the firm HAS. Which template
belongs to which shelf product is a judgement only an operator can make, so the column is set from
the admin products page and never inferred. The product settings sheet lists templates for the
tenant's enabled SEI firm (`platform.tenant_platforms.config.firm_id`) and writes
`account_open_reference` through `catalogue.set_product_platform`. The rest of that panel is still
read-only reference data; this column is the one exception, because it is a choice rather than a
fact about the custodian.

**No `platform.provider_reference` cache.** An earlier draft of this document proposed one,
seeded from the workbooks where no endpoint existed. §5.1 removes the need: the drop IS the
cache, `ref.codes` is already the conformed view of it, and the three genuinely missing sets
(§5.2) are either read on demand or configuration. Building a second reference store beside
`ref.codes` would give two sources that drift.

---

## 6. Schema gaps, consolidated

Revised against §5: several of these looked structural and turn out to have a source in the
nightly drop, which changes them from integrations into modelling jobs. Ordered by how much is
blocked behind each.

| #   | Gap                                                                                                                                                                                                                                                                                         | Blocks                                                            | Where it stands                                                                                                                                                                                                                                                                                                                                                                                                                              |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **No mandate entity.** `platform.accounts.payments`/`.withdrawals` are jsonb that nothing writes.                                                                                                                                                                                           | all of §4.3, the drawdown income leg of §4.4                      | **Source found.** SEI's `raw.rec_cash_activities` (55 columns) already lands the whole shape; Seccl publishes it as `recurringPayments` on the account. So this is `platform.mandates` — account, kind, amount, frequency, `payment_day`, start/end, auto-invest/disinvest, `provider`, `provider_reference`, `next_due_date` — plus a conform step, not an integration.                                                                     |
| 2   | **No provider-employee identity for staff.** `crm.client_advisers.adviser_id` is an `auth.users` uuid; SEI wants `employeeEntityId`. `platform.links.entity_id` is `bigint` and **cannot hold a uuid**.                                                                                     | the SEI enrolment DSR block (§4.1, §5.3)                          | Still open. The VALUES are in the drop (`UserDetail.ENTITY_ID` + `UserTeamRoleDetail`), so this is a join we cannot express, not data we lack. Either widen `platform.links` with a nullable `entity_uuid`, or add `auth.user_provider_identities (user_id uuid, provider, external_id, role)`. The second is cleaner: a staff↔provider identity is not a book link.                                                                         |
| 3   | **Enrolment templates had nowhere to live.**                                                                                                                                                                                                                                                | opening a SEI account at all; opening a Seccl PENSION             | **Closed** — migration `0116_account_open_reference` (§5.4) plus `ref.enrollment_templates` for discovery.                                                                                                                                                                                                                                                                                                                                   |
| 4   | **No policy link for Morgan Lloyd.**                                                                                                                                                                                                                                                        | §4.9 valuations                                                   | Still open, and it is the smaller half of that blocker: a `platform.links` row with `link_type='policy'` (0066's key permits a second row per account). The other half is #5.                                                                                                                                                                                                                                                                |
| 5   | **No instrument-level valuation.** `platform.valuations` is account × date.                                                                                                                                                                                                                 | §4.9 `FundSplits`                                                 | Unchanged and deliberate: read the lake behind a refusing seam. Do not add a positions table to OLTP — that decision is `docs/provider-pipeline.md`'s and stands.                                                                                                                                                                                                                                                                            |
| 6   | **No delivery queue.** Nothing turns a settled `platform.instructions` row or a booked contribution into a provider call with retry semantics.                                                                                                                                              | every write in §4 beyond the four built paths                     | `docs/provider-routing.md` §6a is the design, and it was **built and then deliberately removed**: `0074` shipped the table, claim/backoff/idempotency, retry classes, a worker loop and a test suite, and `0077` dropped the lot because `provider_queue.enqueue()` was called from nowhere but its own tests. Rebuild it **with** the producer. The design needs no revisiting.                                                             |
| 7   | **FCA advice-and-guidance context is not held.** ML's nine `cam_drawdownsetup` flags; Seccl's `advised`, `guidance`, `fcaIndividualReference`, `benefitsTakenBefore2006`, `protectedRetirementAgeOrLumpSum`.                                                                                | §4.4 at both vendors                                              | Still open. These are properties of the ADVICE EVENT, not of the client, so they belong on a `platform.crystallisations` record beside `contributions` and `transfers` — same shape, same lifecycle discipline. One gap, not two: both vendors ask the same questions.                                                                                                                                                                       |
| 8   | **Lump-sum allowance running totals.** `tfcUsed`, `individualLumpSumAllowanceUsed`, `lumpSumDeathBenifitAllowanceUsed`.                                                                                                                                                                     | §4.4 — `tfcUsed` is **required** by `cam_drawdownsetup`           | Same table as #7. `crm.contacts.pension_protection[]` already holds the protections that change the allowance, and SEI's `crm/sipp-accounts` shape (`bces[]`, `protections[]`, MPAA) is the most complete statement any vendor gives of what to model.                                                                                                                                                                                       |
| 9   | **No portfolio entity.** Every SEI cash and model call takes a `portfolioId`, and an account can have a Capital and an Income portfolio.                                                                                                                                                    | §4.2, §4.5                                                        | **Source found**, which halves it: the nightly `Accounts` report is grained account × portfolio and carries `PORTFOLIO_ID`/`_TYPE`/`_NAME`/`_PURPOSE`. Today it is a `platform.links` row with `link_type='portfolio'`, which permits exactly one. The fix is a real child table, not a discovery exercise.                                                                                                                                  |
| 10  | **Transfer facts missing.** `cam_pensiontype` is REQUIRED by `cam_createtransfer` for a pension policy; Seccl's `productDrawdownStatus` and `transferAccountAddress` are optional.                                                                                                          | §4.7 — a pension transfer cannot name the ceding product type     | **Closed** for the fields the raise drawer collects (Sept 2026, `0176_transfer_pension_facts`). `basis`, `extent`, `pension_type`, `crystallisation` and `safeguarded_benefits` are columns. A Seccl account sends the client's address as `transferAccountAddress`, and the FCA reference when safeguarded rights are set. An SEI `transferIn` still needs the signing authority and the ceding account type. It does not need asset lines. |
| 11  | **No beneficiary/nomination entity.**                                                                                                                                                                                                                                                       | Seccl `wrapperDetail.nominations[]`, ML `cam_createbeneficiaries` | Still open. The nearest thing is `crm.account_contacts.role`, whose `crm.account_role` enum carries `beneficiary` — but it holds no proportion, and a nomination without a percentage of fund is not a nomination.                                                                                                                                                                                                                           |
| 12  | Small ones: `crm.contacts` has no `country_of_birth`, no FATCA fields, no `flexi_access_date` (0053 stores the boolean only), no client-level risk rating; no tenant/staff FCA reference number; `platform.instructions.amount` is `numeric(18,2)` against `numeric(20,4)` everywhere else. | assorted required and optional fields                             | Mostly one column each. Note SEI's `Clients` report carries `COUNTRY_OF_BIRTH`, `CERTIFIED_FATCA_COMPLIANCE`, `CLIENT_SUBJECT_TO_FATCA` and `CLIENT_RISK_RATING`, so for a client already at SEI these are observable rather than unknown.                                                                                                                                                                                                   |

One gap that closed itself while writing this: **`firmInvestmentCapacityId` needed no code map**.
Reference `FIRM_INVESTMENT_CAPACITY` is 3 Discretionary / 4 Advisory / 5 Execution Only, which is
`crm.service_type` after migration 0104.

---

## 7. Corrections and findings

Established against the specs, the code and a real p04 drop. Listed so they are not re-derived.

### Fixed in this change

1. **`valuations.py::push_scheme_valuation` called `cam_updateschemevaluation`, which does not
   exist** in the org's `$metadata` or the published docs — and it was wired into
   `services/morgan-lloyd`'s scheduled `sink.daily_valuations`, so its first live push would have
   failed with a vendor error that reads as a permissions problem. Its own docstring said it was a
   placeholder, and a mocked test _pinned the placeholder_. Replaced with the documented
   `cam_createpolicyvaluation` and typed `PolicyValuation`/`FundSplit` models that refuse a policy
   value disagreeing with its fund splits (the vendor states the equality and does not say what it
   does otherwise, so the alternatives are a wrong valuation on a client's record or an error
   naming neither number). The sink and its test moved with it.
2. **`cam_payrequest` does not exist either**, and nothing called `pay_request`. Removed — a method
   naming a non-existent action is worse than no method, because Dynamics answers an unknown
   unbound action with a generic failure that looks like a payload problem.
3. **`pkgs/morgan-lloyd/openapi.json` had been moved to `spec/ml.openapi.json`** without updating
   `scripts/build_openapi.py` or `tests/test_catalogue_scripts.py`, so the drift test was failing
   on a missing file. Both now point at the new path, which also puts the three vendor specs in
   one relative location (`pkgs/{morgan-lloyd,sei,seccl}/spec/`).
4. **My own Seccl `add_asset` used the wrong URL convention** — `POST /asset/{firm}` where the
   collection documents `POST /asset`. Found by the tripwire on its first run, which is the
   argument for having written it.
5. **The Postman collection's filename**: it had been renamed to `seccl.openapi.json`; it is back
   to `seccl.postman_collection.json`, which is correct. Seccl publishes no OpenAPI document — the
   collection is the whole contract, and `scripts/build_schemas.py` exists because of that.

### Standing facts

6. **The SEI merged spec's `servers` are the non-mTLS gateway** and we call the mTLS hosts
   (`pkgs/sei/src/sei/settings.py::ENVIRONMENTS`). Anyone generating a client from the spec will
   point it at a host that refuses our certificate.
7. **The API sheet and SEI's own Swagger disagree on one path's casing** —
   `getAccountDetailsInfo` on the sheet, `getAccountDetailsinfo` in
   `swagger-spec-RetrieveAccountCharacteristics.yaml` and in `x-sei-original-path`. The vendor
   manifest carries the sheet's spelling, so anything generated from it will 404.
8. **`app/integrations/sei/cash_receipts.py` is wrong about `portfolioId`.** Its docstring says
   nothing derives it and it "arrives from SEI onboarding"; the nightly `Accounts` report is
   grained account × portfolio and carries it (§5.1). The comment should be corrected when that
   module is next touched — it is the reason the portfolio link is modelled as a single
   `platform.links` row.
9. **No workbook matches its own environment.** `accountClassificationId 48518` is in a request
   that succeeded and in no workbook (it _is_ in the Reference report);
   `accountTemplateId 2981141` is in i03 only. Treat all three as evidence, never as an
   enumeration.
10. **`docs/morgan-lloyd/data-mapping.md` is written against a schema that no longer exists** — it
    references Drizzle (`pkgs/db/src/schema.ts`), an `applicants` table,
    `accounts.platformIdentifier` and `beneficiaries.mlBeneficiaryId`. The mappings are
    substantially right; the column names are from a previous data model. Its `platform.links`
    table and the CTC XML outline are the parts worth keeping.
11. **SEI's PME model assignment appears at two paths each**
    (`pmemodelassignmentservices/PSM/…` and `pmemodelassignment{variant}/PSM/…`) — a merge
    artefact, not two operations. `pkgs/sei` uses the `pmemodelassignmentservices/` form.
12. **Which model-assignment variant applies is decided by `MODEL_TYPE`**, and the firm's real
    models are all type 8: `assignment/investment`, not the implementation or allocation variants
    the API sheet happened to name. Reference `MODEL_TYPE` is the vocabulary (8 Investment,
    4 Implementation, 3 Asset Allocation, 5 Strategy).
13. **Fees cannot be kept in step with Morgan Lloyd through the API** — ML has no fee endpoint. A
    fee-package edit that matters to an ML-produced document is an operational hand-off and should
    raise a task, not diverge silently.
14. **A settings class must read both `.env` and `.env.local`.** `MorganLloydSettings` declared
    `env_file=".env"` alone and silently built unconfigured objects; fixed and pinned by
    `pkgs/morgan-lloyd/tests/test_settings_env_files.py`, which covers **ML only**. `SecclSettings`
    declares both with no test; `SeiSettings` declares one and gets away with it because `cred()`
    reads `os.environ` first.
15. **`services/sei/.env` is fully configured for i31.** An earlier revision of this document
    claimed `SEI_PGP_PRIVATE_KEY_BASE64` and `SEI_PFX_BASE64` were unset; both are set (9,016 and
    3,908 characters). The claim came from a key-scan regex of `^[A-Z_]+=`, which excludes digits
    and therefore silently skipped every variable ending in `BASE64` — a reminder that "the
    variable is absent" and "my scan cannot see it" look identical in the output.
16. **The certificate is `SEI_PFX_BASE64` only.** `SEI_PFX_PATH` was removed (Aug 2026) from
    `pkgs/sei`, `services/sei`, both env files and the API's gap-fill list. A path needs a file
    on the host and a bind mount, which a one-env-list deployment has nowhere to put, so nothing
    deployable used it — while carrying both forms meant two precedence rules that had already
    drifted: `SeiSettings.configured` asked for the PATH where `ssl_context` and `pfx_material()`
    preferred the base64, so a container supplying only base64 was refused naming the one
    variable it could not set. `apps/api`'s `cash_receipts._settings()` had the same bug one
    layer up — its `.env`/`.env.local` gap-fill list named `pfx_path`, so a `SEI_PFX_BASE64`
    placed where every other API secret lives was never picked up. One form, one check, one name
    in the error. The PGP key keeps its path fallback: it is developer-facing and a 9KB armoured
    key is awkward to paste.
