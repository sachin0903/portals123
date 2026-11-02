# Integration tests against the custodians' own test environments

```
moon run morgan-lloyd:integration-test     # Clifton's dev Dynamics org
moon run seccl:integration-test            # pfolio-api-staging.seccl.tech
moon run sei:integration-test              # SWP i31 (test.mtls.api.seic.com)
moon run :integration-test                 # all three
```

**Never part of a gate and never in CI.** Nothing depends on these tasks, they are
`runInCI: false`, and they are excluded from a bare `pytest` as well — see "Why the exclusion is
in the config" below, because that detail is the one that keeps a live call out of the build.

## What they are for, and what they are not

Every other test in these packages asserts on a payload we built or a document we transcribed.
Those are necessary and they share one blind spot: they cannot tell you the counterparty still
agrees. Each of the three integrations has lost real time to exactly that —

| what broke                                                            | why a mock could not see it                                                                                                                           |
| --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cam_updateschemevaluation` — an action name that does not exist      | it was wired into a scheduled sink and pinned by a mock that answered anything                                                                        |
| `cam_schemerefs` vs `cam_schemes` — two populations, **zero** overlap | every scoped call was refused with a message that reads as "that scheme does not exist"; two rounds of "fixing" a model that had never produced a row |
| the action envelope — the id is nested under `response`               | the contact was created every time and the id discarded, so each retry duplicated a person                                                            |
| SEI restricts by **source IP**                                        | "only works from Coolify" was believed for a fortnight; it works from a laptop on the VPN, and only a call could establish that                       |
| Seccl's fee register is **empty** on staging                          | a model that infers its columns from rows has none to infer from                                                                                      |

So these probe the VENDOR's behaviour: does auth work from here, do the actions exist, do the
option sets still hold, does the response still have the shape our parsers assume.

They are **not** a second copy of the domain tests. `app/integrations/*` is covered by fakes and
stays that way — those tests are about our own decisions (what to send, when to refuse, what to
record), and a live call would make them slow, order-dependent and reliant on somebody else's
sandbox holding still. What a fake cannot answer is the vendor half, and that is all this is.

## Credentials: they live with the SERVICE, not the package

Each `integration-test` task carries `envFile: ['/.env', '/services/<vendor>/.env']`. The settings
classes read `.env` relative to the WORKING DIRECTORY, which under moon is the package — where
there is no `.env` and never should be. The real credentials are in `services/<vendor>/.env` (what
the sync uses) and `apps/api/.env.local`; moon loads them into the process env, which
pydantic-settings prefers over its own `env_file` anyway. A file that is not there is skipped, so
a fresh clone gets the guard's "credentials are not set" rather than a task that cannot start.

All three configured environments are, as it happens, exactly the sandboxes on the allowlists —
`cliftonassetdev.crm11.dynamics.com`, `pfolio-api-staging.seccl.tech`, SEI `i31`.

**SEI additionally needs the company VPN.** It allowlists by SOURCE IP, which no other vendor here
does, so an off-VPN run gets HTTP 401 from a perfectly good credential. That is treated as an
environment being unavailable rather than as a defect: one clear message naming the VPN, through
the same `_refuse` path as a missing credential, instead of the same 401 repeated per test.

## Three guards

**1. Credentials, or a failure naming what is missing.** A silent skip on a task you invoked
deliberately reads as "the integration is fine", which is worse than a red. Set
`ALLOW_SKIP_INTEGRATION=1` to skip instead — useful for `moon run :integration-test` on a machine
with one vendor's credentials.

```
$ moon run morgan-lloyd:integration-test
Failed: Morgan Lloyd credentials are not set: MORGAN_LLOYD_TENANT_ID, MORGAN_LLOYD_CLIENT_ID,
MORGAN_LLOYD_CLIENT_SECRET — set ALLOW_SKIP_INTEGRATION=1 to skip instead of failing
```

**2. Non-production, and FAIL-CLOSED.** The endpoint has to be on an allowlist of known
sandboxes; anything unrecognised refuses. Not a "does this look like production" test, which is
the wrong way round — this is the posture `app/email.py`'s send guard takes, and for the same
reason: the absence of recognisable configuration must mean "do nothing", never "do it to
everyone". A misspelt or newly-issued host lands on the guarded side, and adding a sandbox is a
deliberate edit.

**This refusal is not overridable by `ALLOW_SKIP_INTEGRATION`.** A missing credential is an
environment that happens to be unavailable; a wrong host is a configuration error.

| vendor       | allowed                                                       | refused                                         |
| ------------ | ------------------------------------------------------------- | ----------------------------------------------- |
| Morgan Lloyd | `cliftonassetdev.crm11.dynamics.com`                          | anything else — production differs by one word  |
| Seccl        | `pfolio-api-staging.seccl.tech`                               | production is one hyphenated word away          |
| SEI          | environment `i10`/`i31` **and** host `test.mtls.api.seic.com` | `p04`, and any host the mapping did not produce |

SEI is checked twice on purpose: `SEI_BASE_URL` overrides the environment mapping, so an `i31`
label can be pointed at production, and a `p04` label is production whatever the URL says. Either
check alone would be a guard with a way around it.

**3. Writes are off unless asked for** (`INTEGRATION_ALLOW_WRITES=1`). Everything in a default run
is a read. The reason differs by vendor and is worth knowing:

- **Morgan Lloyd has no delete.** A create is permanent in a real firm's sandbox. This is the same
  fact that makes `retry='manual'` the default for its vendor operations and makes `sync_client`
  check `platform.links` before every step. There is no cleanup and the tests do not pretend
  otherwise — records are named `ZZ-INTEGRATION-<timestamp>` so a human can see what they were.
- **Seccl has delete**, so a write is recoverable; the gate is about not littering staging.
- **SEI's writes are instructions about money** — a cash receipt, a disbursement, an enrolment —
  so the default is reads even though the environment is a test one.

## Why the exclusion is in the config, not the task

`addopts = "-m 'not integration'"` sits in each package's `pyproject.toml`, and the task passes
`-m integration` to override it. Backwards from the obvious arrangement, deliberately: the default
has to be the safe one wherever pytest is invoked — a developer typing `uv run pytest` in the
directory, an editor's test runner, a future task that forgets the flag. Excluding them in the
task's arguments would leave every one of those reaching a live counterparty.

The marker travels with the test, so a file moved or added anywhere in the package is still
excluded. `moon run <pkg>:test` reports the count, which is the cheap confirmation:

```
morgan-lloyd:test | collected 328 items / 61 deselected / 267 selected
       seccl:test | collected 175 items / 29 deselected / 146 selected
         sei:test | collected 170 items / 27 deselected / 143 selected
```

`cache: false` on the task, and that is not decoration either: a cached pass would say the
counterparty still behaves this way when all that is established is that it did once, and
re-asking is the entire value.

## What the first live run found

Worth recording, because it is the argument for the whole exercise. Every one of these was
invisible to the existing suites, and several were in the tests themselves — which is the point:
a test that has never run against the counterparty is a hypothesis.

**In our code, found by the vendor:**

- **`cam_payrequest` is documented and does not exist.** The vendor's Benefits section documents
  `POST /cam_payrequest`; it is neither an action nor an entity set in the org's `$metadata`, and
  nothing uses it. Recorded in `KNOWN_ABSENT`, with a companion test that fails if it appears.
- **Seccl's inferred requiredness is wrong for eight fields.** 222 of 288 live staging clients
  fail validation against the generated `Client` model — `updateHistory.updateAction`,
  `amlData.checkDate` and six more. `pkgs/seccl/README.md` predicted exactly this ("required means
  present in every example… decent evidence and not a guarantee"). Harmless today because nothing
  validates a live read through those models; it would be a 500 the day something did. Recorded in
  `KNOWN_OVER_REQUIRED`; the real repair is `build_schemas.py`'s inference rule.
- **Seccl sends `dateOfBirth` in two shapes.** 285 records carry
  `"1986-04-23T00:00:00.000Z"` and 3 carry a bare `"1972-04-25"`. The conformed model already uses
  `try_cast(… AS date)` so the pipeline is fine — it is the generated model that is wrong about
  the vendor. Recorded in `KNOWN_TYPE_DIVERGENCES`, which names each type divergence individually
  rather than allowing them as a class.
- **`pkgs/seccl` did not declare `cryptography`.** Its client-assertion flow signs RS256, which
  pyjwt cannot do without it (`NotImplementedError: Algorithm 'RS256' could not be found`); the
  package declared bare `pyjwt` and authenticated only because something else in the shared
  workspace venv happened to pull `cryptography` in. A standalone install could not have logged
  in. Fixed — the dependency is `pyjwt[crypto]` now.

**In the tests, found by the org — the assumptions a live call disproves:**

- **The client raises its own error type.** `_base._request` wraps an HTTP failure into
  `MorganLloydApiError`, so `httpx.HTTPStatusError` never escapes; the tests caught the wrong
  thing and every 403 probe failed.
- **An entity's attributes are NOT an action's parameters, and only the action decides what a
  payload may contain.** The org's `contact` entity HAS `cam_additionalvulnerabilitynotes` and has
  NO `cam_detailssupportingnotes` — read alone, that says the API sends a field the org lacks. The
  ACTION `cam_createcontact` declares the opposite, and the action is what we POST. Checking the
  entity was the wrong surface; the suite now asserts against the action's declared parameters and
  their types, which is the authoritative check and catches the whole class.
- **The wrong scheme reference does not raise — it answers EMPTY.** The suite asserted a series
  reference would be refused. `cam_getpensioncontributionapplications` answers
  `{"contributionApplications": [], "message": "No Contribution Applications found for Scheme …"}`
  — a successful call. Which is precisely why four models landed empty for a run of releases and
  it read as "this firm has no schemes". The assertion is now about the shape of the emptiness,
  and about the leading-"no" anchor `_is_empty_result` depends on.
- **The manifest's `action` field is not always an action.** A `PATCH cam_personalpolicies(<id>)`
  carries the entity set there, so comparing all of them against the action list reported five
  healthy entity sets as missing.

## The lifecycle suites, and why they reuse everything

Each vendor has a `test_live_lifecycle.py` walking one subject end to end, in the vocabulary that
applies to that vendor. They are siblings, not copies: Morgan Lloyd is a scheme ADMINISTRATOR and
SEI and Seccl are CUSTODIANS, so the same shape is spelled differently.

|             | Morgan Lloyd                                   | Seccl                                   | SEI                                     |
| ----------- | ---------------------------------------------- | --------------------------------------- | --------------------------------------- |
| the subject | adviser → client → scheme → policy             | node → client (fresh per run) → account | account (configured)                    |
| money in    | contribution (amend, stop)                     | payment in, one-off and regular         | cash receipt: ACR, and a standing order |
| money out   | drawdown (DCube SSO)                           | withdrawal, one-off and regular         | disbursement (pay-to recipient first)   |
| the model   | —                                              | attached at account create              | assigned per PORTFOLIO                  |
| the fee     | —                                              | `feeStructures.adviserFeeId`, fee group | fee package, by numeric account id      |
| also        | valuation, illustration, transfer, beneficiary | cancel a pending withdrawal             | cancel the activity                     |
| status      | **60 pass, 1 skip**                            | **27 pass, 2 skip**                     | **7 pass, 21 skip — 27 Sep 2026**       |

**REUSE, WHERE REUSE IS THE RIGHT ANSWER** — and it is not the same answer for all three.

- **Morgan Lloyd anchors everything**, because Dynamics has no delete: a record is permanent in a
  real firm's sandbox. Verified by counting either side of a run — `contacts` 33, `cam_schemes` 6,
  `cam_policies` 10, identical before and after.
- **Seccl creates a fresh CLIENT and ACCOUNT every run** and reuses only the MODEL. Seccl has
  delete and a client is cheap, and a fresh one buys real coverage: the create path is exercised
  every time rather than once against an empty firm, and the whole chain (create → accept terms →
  Active → money) is proved from nothing rather than proving that yesterday's client still works.
  A model belongs to the FIRM, so one per run would grow its model list for ever with no upside.
- **SEI creates nothing**, because it cannot: there is no create-an-account call.

Anchors are discovered from the API, never from local state: a committed id rots when somebody
resets the sandbox, and a cache file makes a fresh clone behave differently from a warm one — the
property least wanted in a test that already depends on somebody else's environment. What IS
cached is the resolved id as a string, for the life of the process; fixtures stay function-scoped,
because a session-scoped async fixture holds an `asyncio.Event` bound to one event loop and
pytest-asyncio gives each test its own (`ScopeMismatch`, then "Event bound to a different event
loop").

The one thing a run necessarily adds is a money INSTRUCTION, because an instruction is an event
rather than a record and cannot be found-or-created. So the last step cancels what the run raised
(`request_deletion`), and the account finishes every run with **zero pending** — the cancelled
deposits remain as `Deleted` history, which is a record of what happened rather than something in
flight.

### SEI needs an account, and there is deliberately no default

`SEI_INTEGRATION_ACCOUNT`, plus `SEI_INTEGRATION_PORTFOLIO` when the account holds more than one.
Without it the money tests skip, naming the variable.

A default would be easy — the synced drop in `.data/sei.duckdb` carries thousands of real account
numbers and its `FIRM_ID` matches these credentials. That is exactly why there isn't one: the
accounts on i31 are not obviously fictional, and a zero-config default means anyone running with
writes enabled sends **instructions about a real person's money** — a cash receipt they did not
expect, a disbursement out of their account, a fee. A skip that names the variable is the safe
failure.

### The vendor rules that make anchoring necessary

Not tidiness — each of these makes a create-every-run suite fail on its second run or leave a mess:

- **Morgan Lloyd: a NI number is unique across contacts** (`0x80040224 The ni number is already
used for an existing contact`), so a probe with a fixed NINO passes exactly once. And
  **`QQ123456C` — the canonical HMRC example, which every fixture in this repo reaches for — is
  REFUSED** (`does not conform to the UK standard`); the `TN…A` shape works.
- **Morgan Lloyd: a scheme type can only be set up once per contact** ("A scheme of this type has
  already been setup for the contact") — the business rule once mistaken for evidence that the
  create path worked.
- **Seccl registers an EMAIL to exactly one client** (`400 email: Email already registered`) —
  the same rule in a different vendor's clothes, and the reason a fixed address makes a create
  path testable exactly once. The run's marker goes into the address, and a second client in the
  same run takes a suffix, because the contract suite creates one while the lifecycle creates its
  own.
- **Seccl has no `lastName` field**; it is `surname`. `lastName` is ignored on the way in and
  absent on the way out, so a payload built around it fails as "Surname can't be blank" and a
  lookup on it never matches.

### What is NOT covered

**CTC illustration generation.** `apps/api/app/integrations/ctc` builds and validates CTC1 XML
against a different vendor at a different host, with 43 tests against the spec's own examples. The
Morgan Lloyd half — attaching a template to a benefit application — is covered.

**SEI's disbursement and ad-hoc fee are written but not sent**, each with the reason in the skip: a
disbursement pays real money to a real recipient profile on that environment, and an ad-hoc fee
takes money out of a client's account and is not reversible through the API. **SEI's model
assignment is blocked on `toleranceMultiplier`**, which is mandatory on all three assignment
variants and has no source — SEI refers to its own `MODEL_RAIL_MULTIPLIER` table, which is in
neither the nightly drop nor any lookup service. Recorded as a skip rather than omitted, so "is
model assignment covered?" has an answer.

## SEI: it was never the VPN, and finding that out took a probe

The suite refused every test with `SEI refused this machine … SEI allowlists by SOURCE IP —
connect to the company VPN`. That message was wrong, and it was wrong in the most expensive way
available: it named a plausible cause, so nobody looked further.

Separating the three credentials by hand settles it in four calls:

```
1. TCP 443                        OPEN
2. OAuth token                    HTTP 200   token acquired
3. /heartbeatapiproxy/status      HTTP 200   {"Status":"OK"}
4. /cachereadservices/…           HTTP 401   {"message": "Invalid ApiKey for given resource"}
```

The token endpoint and the heartbeat are on the same host behind the same allowlist. **They
answered 200 from the machine that was supposedly not allowlisted.** The credentials are byte-identical to the
monitoring repo's own `SEI_I31_*` set (compared by hash), and the PFX password matches.

**SEI has FOUR failure modes and they are easy to confuse. Only the first is ever about us.**

| what SEI answers                                    | what it means                                               | whose problem     |
| --------------------------------------------------- | ----------------------------------------------------------- | ----------------- |
| TLS refusal, connect hang                           | the client certificate, or the IP allowlist                 | ours              |
| `401 Invalid ApiKey for given resource`             | this AppKey is not entitled to that RESOURCE                | SEI's — a grant   |
| `404 Unable to identify proxy` / `unknown-resource` | no proxy is DEPLOYED for a path SEI's own OpenAPI documents | SEI's             |
| `500` / `503`                                       | SEI is up, this service is not                              | SEI's — an outage |

Three things came out of it, all in the client rather than the tests:

- **`_from_status` collapsed every 401 into one message** — "Check the AppKey, the OAuth user, and
  that the caller's IP is allowlisted" — for a body that says precisely which of those it is.
  There is a `SeiEntitlementError` now (a subclass of `SeiAuthError`, so every existing handler
  keeps working) whose message names the resource and says the credentials and network are fine.
- **There was no heartbeat method**, so the suite's reachability probe used an ordinary resource —
  and picked one this AppKey is not entitled to. `/heartbeatapiproxy/status` is the only SWP call
  needing no entitlement and no firm, which makes it the only honest liveness probe; it is what
  `monitoring` has always used. Using anything else makes a fact about ONE endpoint look like a
  fact about the network.
- **`SeiSettings` printed `appkey`, `appsecret`, `oauth_password` and `pfx_base64` in plaintext**
  into any pytest traceback that took it as a fixture argument — pydantic renders the whole model
  in a repr, and one failing test posted the live credentials into a terminal. Every secret is
  `repr=False` now. `pfx_base64` is the private KEY, so it was the worst of them.

### What this AppKey can actually reach on i31 (27 Sep 2026)

Re-probed read-only from this machine. The 06 Sep map — heartbeat only, everything else a 401,
a 503 or a missing proxy — is no longer true.

The first probe used firm **3102**, Blackfinch. Select is **3106**. The suite calls 3106 unless
`SEI_INTEGRATION_FIRM` says otherwise. Client templates on 3106 include "Select Platform -
Client (Individual)" and "Select Platform - Client (Organisation)". Account templates have no
proxy on any of the six firms.

Entitled, and answering: the heartbeat, client enrolment templates, the system-code cache (the
call succeeds; the ActivityState set comes back empty), account details, realtime holdings,
activities, fee-package details, entity search, and platform availability. A bad account number
on those reads is a domain 400 with `errors[]`, not an entitlement 401.

Still not usable:

- `enrollmentAccountTemplates` — the spec's casing has no proxy. The lowercase
  `enrollmentaccounttemplates` returns Select's 38 account templates, so `accountTemplateId`
  is readable. `getFirmDsrs` is still HTTP 500, and it does not need to succeed: the SFTP
  drop already carries `DSR` and `DSR_TYPE_NAME` (64 DSR codes for firm 3106).
- `getStrategyList` — entitled, and every body we can form is rejected (gateway 400 for the
  object envelope, 500 for the spec's array envelope).
- `getFirmDsrs` — HTTP 500.
- `firmemployeeusers` — HTTP 400, "Processing Organization Id is required", including when that
  query parameter is sent.

The money tests still need `SEI_INTEGRATION_ACCOUNT` and have not run. There is deliberately
no default. Re-run on 27 Sep 2026: **7 passed, 21 skipped**. The new passes are the client
templates (they carry an id) and a domain refusal that now comes back with `errors[]`.

A `conftest` hook converts an escaping `SeiEntitlementError`, a 5xx or a gateway
route-fault into a skip for every test at once — applied as a hook rather than a `try` per test so
it cannot be forgotten, and taking the posture `docs/morgan-lloyd/endpoint-coverage.md` takes for
the 85 forbidden `cam_*` tables: a permission we do not hold is a request to the vendor, and a
suite that is permanently red about it stops being read. **A plain 401 still fails** — that one is
ours.

`TestWhatThisAppKeyMayActuallyReach` then states the gap as ONE finding rather than twenty quiet
skips, and it is INVERTED like the Morgan Lloyd permission test: it passes while a resource is
unavailable and FAILS the day one is granted. That inversion earns its keep — `cam_createscheme`
sat blocked for a month with correct code on our side, and a red test is how the grant was noticed.

**The money tests still need `SEI_INTEGRATION_ACCOUNT`** and have not run: most of the skips are
that, and there is deliberately no default (see above). Even with one set, the reads they depend
on are behind the entitlement gap, so the useful next step is the grant, not the account number.

**Point it at a GIA** — `ACCOUNT_TYPE` 157 Investment Account with no pension classification, the
wrapper with the fewest preconditions and the one this environment has most of. An ISA carries
subscription rules and a JISA a registered-contact rule, both of which fail a money instruction for
reasons that have nothing to do with the API; a House Account (176) is the firm's own.

### A SIPP at SEI is a CLASSIFICATION, not a wrapper — and the old reading mis-filed 830 accounts

"This environment defines no pension wrapper at all, which constrains what SEI can be used for"
stood in five places in this repo, and `docs/api-integrations.md` drew the conclusion out loud:
_"so there is nothing to hold a SIPP"_. The wrapper half is true. The conclusion is not.

```
ACCOUNT_CLASSIFICATION_NAME      Pension                            934 accounts
ACCOUNT_SUB_CLASSIFICATION_NAME  Pension - HMRC Approved - SIPP     830
                                 Pension - HMRC Approved - SSAS       4
ACCOUNT_QUALIFIER_NAME           SIPP                                14
```

Every one is `ACCOUNT_TYPE` **157 Investment Account**. So the firm already holds 830 SIPPs at SEI,
and they are exactly the custodian-plus-administrator split this estate runs: **SEI holds the
assets, Morgan Lloyd administers the scheme.** Nothing in SEI's account model names the
administrator, because the scheme is not SEI's record to keep — `MLSIPP/…` lives on
`platform.links`.

**The cost of the old reading was not theoretical.** `product_key` mapped from the wrapper alone,
so all 830 landed in the book as GIAs — and `product_type` is what every "is this a pension?"
reader gates on, so those accounts could not be drawn down ("Drawdown applies to pension accounts
only"), could not take a pension contribution, and were tested against the £60k allowance rather
than the MPAA. Fixed in `services/sei/models/platform/accounts.sql`, which now reads the
classification first, and pinned by `services/sei/tests/test_platform_accounts.py` — which
EXECUTES the model in DuckDB rather than parsing it, because the rule is a CASE whose ordering is
the whole point.

**Creating one through the API is blocked, and not by us**, in the order the blockers bite:

1. There is no create-an-account call — an account arrives through the ENROLMENT journey, and the
   classification fields are set there. That one is a deliberate decision of ours (`docs/sei.md`:
   depth 8, 48 leaves, a `clientList[]` array — a journey, not a form).
2. Templates list. `client_templates` and `account_templates` both answer 200 as of 22 Sep 2026
   (account templates are `/enrollmentaccounttemplates/…`; the Swagger camelCase 404s). A POST
   is accepted and returns a BPM handle. Completion is async validation (DSR, tax, citizenship,
   `mailingLabelName`) rather than an entitlement refusal.
3. The AppKey is not entitled to most of what follows.

2 and 3 are SEI's. The suite states the finding without needing a live call
(`test_a_sipp_is_a_CLASSIFICATION_here_not_a_wrapper`) and refuses to instruct money on a pension
account (`anchors.refuse_if_pension`) — a misconfigured `SEI_INTEGRATION_ACCOUNT` does not look
wrong, since a SIPP and a GIA share a wrapper code, and the cost of getting it wrong is a real
pension movement the scheme administrator does not know about.

## What the Seccl lifecycle found## What the Seccl lifecycle found

Six, and two of them were defects in `pkgs/seccl` rather than surprises about the vendor. Every
one was invisible to the existing suites, which validate payloads against models built from the
same examples the payloads came from.

**Two defects in our own client:**

- **`create_client` and `create_account` did not put `firmId` in the body**, while fourteen other
  creates did. `create_client`'s own docstring said the firm goes in the body and the method did
  not put it there, which is the worst arrangement — a caller who read the documentation and
  trusted the method got neither. The failure is opaque: `500 Internal Server Error` with an empty
  `fieldErrors`. With the stamp, the same call answers `404 "Node static data not found"`, an
  actionable problem. **So the missing field did not merely fail, it disguised the next failure.**
  Fixed, and pinned by a static scan over every `create_*` (`tests/test_write_bodies.py`).
- **The client threw away Seccl's own account of a refusal.** A refused write answers with a
  `fieldErrors` array naming each offending field and message; the exception said
  `POST /client failed: Client error '400 Bad Request'`. The body was kept for the audit log and
  never shown to the caller, so finding out which field meant reaching for a raw httpx client —
  which the first live run did three times, on three endpoints. The message now carries
  `field: message`, and the very next run answered `vatable: Vatable can't be blank,
investmentDecision.firmLei: is too long (maximum is 20 characters)`.

**Four about the vendor:**

- **Seccl states its own required fields, and the generated models are wrong in BOTH directions.**
  A near-empty `POST /client` answers `400` naming twelve required fields — `surname`, `language`,
  `email`, `emailVerified`, `mobileVerified`, `currency`, `taxDomicile`, `nationality`,
  `addressDetail{.address1,.country,.postCode}`. The generated `ClientCreate` marks **ten of them
  optional**. The first live run had already found the opposite error (`KNOWN_OVER_REQUIRED`: 222
  of 288 live clients fail eight over-inferred required fields), so the inference is both
  over-required and under-required — and the two fail differently: an over-required field fails a
  read of a good record, an under-required one lets a create go out that the vendor refuses. This
  is `pkgs/seccl/README.md`'s "present in every example is decent evidence and not a guarantee",
  measured. It also vindicates `platform_contracts` NOT enforcing Seccl requiredness.
- **`nodeId` is a LIST on a client and a portfolio group, and a SCALAR on an account.** A scalar
  where a list belongs answers a bare `500`; omitted entirely it is `404 "Node static data not
found"`. And **the node has to be real** — every example in the vendored collection uses
  `nodeId: "0"`, which does not exist on this staging firm (its nodes are `21`, `49`, `40`, `9`…).
- **`status` on an account create is a REQUEST, not a fact — and the way to make it true is on the
  CLIENT.** An account created `"Active"` comes back `Pending`, because its client is Pending with
  `termsAccepted: None`. The obvious repair is `PUT /account` with the collection's own
  `{"updateAction": "AmendStatus", "updateData": {"status": "Active"}}`, which is ACCEPTED (200,
  empty body) and changes nothing — an answer with no effect and no error, the worst shape a
  vendor can offer, and one that had this suite documenting Pending as terminal.

  **It is not terminal.** `PUT /client` with `AmendTermsAccepted` turns the client Active and the
  account follows on its own: the status is a CONSEQUENCE of acceptance, not a field to set. (The
  client PRODUCT also carries `acceptedProviderTerms`, and the account goes Active without it —
  that PATCH is then refused `Record updated by another user`, because accepting the client's
  terms bumps the product's `updateId` too.)

  This is what makes the money rail reachable at all, and it is worth the emphasis because
  everything downstream reads as a vendor limitation until you find it: **a Pending account books
  no transaction, refuses a withdrawal for `Insufficient funds for this transaction`, and shows an
  empty feed.** Three tests were skipping or asserting emptiness on that one cause. With the terms
  accepted, payments in book and the feed is real.

  The withdrawal is still refused, and that one IS terminal here: a payment in books as a Pending
  EXPECTATION, so the cash never actually lands on staging, so `autoDisinvest` has nothing to
  sell. Funding for real needs Seccl's settlement to run, which is their process and not an API
  call. So the suite treats `insufficient funds` as a PASS — it proves the instruction reached the
  money engine and was declined on the balance rather than refused on its shape.

- **The `Account` model had never been validated against anything.** The check scoped its read to
  `clients[0]`, who holds no accounts, so it skipped — a quiet way to assert nothing while
  appearing covered. Read against all 380 accounts it finds four real divergences, recorded in
  account-specific `KNOWN_ACCOUNT_*` sets kept separate from the client ones (an excuse for one
  population must not hide a new finding in the other). The notable one: **an account's `clients`
  is a list of ID STRINGS** (`'02659H7'`) where the model expects `Client` objects, on 398 of 380
  accounts — so it is the normal shape, and anything reading `account.clients[0].firstName` off a
  live list read gets an AttributeError. The others are `cgtData.{realised,unrealised}ProfitLoss`
  sent as explicit `null` against a required float, `positions.assetTaxDetails.taxCalculation` and
  `wrapperDetail.productDetail.flexiAccessDate`.
- **`firstName` filters server-side and `lastName` does not** — `lastName=…` comes back with all
  288 staging clients. Harmless once known; a silently-ignored filter is a wrong answer waiting to
  happen, so the query narrows on the half that works and the match is still made in full.

## Where they live, and why not in `apps/api`

In the package that owns the client (`pkgs/morgan-lloyd`, `pkgs/seccl`, `pkgs/sei`), because the
client is what talks to the vendor and the vendor contract is what these establish. The API's
adapters depend on the packages, not the other way round, so an `apps/api` live test would also
need a database, a tenant and `platform.links` rows — a much larger apparatus answering a smaller
question. Where an API-side parser's assumption matters (the action envelope, the nested id), the
assumption is asserted on the vendor's own response SHAPE here instead.

## Adding a vendor

1. `tests/integration/conftest.py` with the three guards. The SHAPE is shared; the answers are
   not — "is this production" is a Dynamics hostname, a Seccl hostname and an SEI environment
   code, so there is no shared abstraction over three one-line predicates and there should not be.
2. `markers` + `addopts` in `pyproject.toml`.
3. The `integration-test` task, `cache: false` and `runInCI: false`.
4. Confirm `moon run <pkg>:test` reports the new tests as _deselected_.
