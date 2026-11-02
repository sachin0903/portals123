# The provider pipeline — how a custodian's data reaches a firm's book

**Status:** THE path, for **all three custodians** (August 2026) — the whole of Seccl's conformed
layer, SEI's six relations, and Morgan Lloyd's `clients`, each verified against a real book. The
old three-hop path is deleted: `app/fanout.py`, `app/fanout_listener.py`, `app/materialise.py` and
`POST /admin/{fanout,materialise}/run` are gone. This file supersedes `docs/fanout-sink.md`, whose
proposal it overtakes in one important respect — recorded in §3.

## 1. What it replaces

Three hand-offs, each of which reported success while producing an empty book:

| Piece      | Where (all three now deleted)     | What it did                                               |
| ---------- | --------------------------------- | --------------------------------------------------------- |
| Sync       | `services/{provider}` (interlace) | Provider API/SFTP/webhooks → the core warehouse (kept)    |
| Projection | `apps/api/app/fanout.py`          | Core → the tenant's own DuckLake catalogue                |
| Trigger    | `apps/api/app/fanout_listener.py` | Polled each daemon's event log for `run.succeeded`        |
| Sink       | `apps/api/app/materialise.py`     | The tenant lake → OLTP `platform.*` / `crm.*`, row by row |

We spent a day proving how badly the seams fail. A manual `POST /admin/fanout/run` does not chain
into materialise (only the listener does, off a daemon run), so a green fan-out left the book empty.
Then materialise rejected every entity with `InvalidAccessKeyId`, because a rebuilt object store had
forgotten the tenant's scoped credential while provisioning reported `service_user: ok`. Both present
as "this firm has no data", which is indistinguishable from the truth.

## 2. The shape

One chain per provider, per tenant, inside the provider's own interlace project. `pkgs/book-sink`
owns it; `services/{provider}/models/tenants.py` is a two-line declaration that the provider takes
part.

```
platform.{entity}            the provider's conformed layer (docs/platform-model.md §3)
        │                    virtual, full_merge on the published key
        ▼
tenant_{hex}.{entity}        virtual  the firm's slice: one containment predicate over lineage.
                             DAG names strip the held-back `ten_` prefix, so Graphene is
                             `tenant_3b54affd38444414.accounts`, not `tenant_ten_…`.
        │
        ▼
stage_{hex}.{entity}         table    the book's own column names, delivered into
                             inbound_{provider}.{uid}__{entity} in Postgres
                             (replace = ReplaceInPlace). One schema per custodian —
                             `inbound_seccl`, `inbound_sei`, `inbound_morgan_lloyd` — not one
                             shared `inbound`. These stay: the Python merge opens Postgres, not
                             the in-session DuckDB that holds unpromoted slices, so it cannot
                             read them.
        ▼
book_{hex}.merge_ledger      virtual  the merge, and what it did, as a warehouse table
lake_{hex}.projection        virtual  the firm's OWN catalogue, refreshed, and its manifest
```

Three decisions inside that are easy to get wrong:

- **The predicate is inlined into the SQL, not passed in.** interlace fingerprints a model from its
  SQL text (`inspect.getsource` for a Python model), and closures are not part of it. A shared
  predicate would make every tenant's model textually identical — and a firm whose org keys changed
  would never rebuild, because nothing about it had changed as far as the planner could see.
- **The merge is a Python model with no parameters.** It declares the staging models as
  `depends_on` so it runs after them, but taking one as a parameter would pull the whole slice back
  through Arrow purely to establish an ordering the graph already knows.
- **The staging projection is thin.** It renames conformed columns to the book's names and nothing
  else. A value that needs deriving belongs in the provider's conformed model, where it is one
  statement for every tenant and is executed in DuckDB by that service's tests.
- **The landing schema is the provider's.** Three daemons ReplaceInPlace one table, and the merge
  reads it later with no column saying who staged it. On 27 Sep 2026 SEI's merge ran three seconds
  after Seccl's and created 304 contacts that were Seccl's people, plus 52 of Seccl's models. The
  table name cannot grow a provider token: `{uid}__third_party_providers` is already 60 of
  Postgres's 63 bytes, and a silent truncate would make two entities share one table. The schema
  is a separate identifier (`0209`). A tenant reset treats `inbound` and every
  `inbound_{provider}` the same way: they are staging, not book tables, and the drop runs as
  `book_writer` because that role owns them.
- **A parent that was NULL is filled when it can be resolved.** Name, status and the other
  insert-only columns stay the firm's once written. `contact_id`, `product_id` and `model_id`
  are different: an account often lands before its client, and a NULL there means the parent
  did not exist yet. The next merge that can resolve it sets the column, and an id already
  stored is not moved.

`materialise: table` is interlace 2.0's external plane: it delivers into a table it never drops
(`replace` is ReplaceInPlace — empty and re-fill), so the landing table's grants and any reader
survive. The write reaches Postgres through DuckDB's own `postgres` extension, via the `book` alias
each provider's `interlace.yaml` attaches.

Virtual snapshots (`platform.*`, tenant slices) use **`full_merge`** on their published key, not
`replace`. `replace` dropped the DuckLake table mid-apply, which is what made lake projection see
`Table with name nodes does not exist`. `full_merge` still deletes vanished keys; it does not drop
the relation. Incremental series (Seccl valuations, webhook staging) stay `merge`. Keyless Python
extractors (Seccl dashboard singletons, Morgan Lloyd scoped actions) stay `replace`. Sync is
`POST /runs` — the models list's rebuild/refresh badge is the compiled plan, not `interlace apply`.

## 3. Credentials — where this diverges from the proposal

`docs/fanout-sink.md` §3 said the daemon "has no connection to the portals DB and shouldn't grow
one", and proposed a scoped `GET /internal/fanout/targets` endpoint instead.

It grew one. The control is **grant scope**, not an API seam:

- `book_writer` (bootstrap `01-roles.sql`, grants in `0062_pipeline_inbound.sql`) reaches the merged
  relations, `platform.merge_rejects`, `auth.tenants`, `platform.tenant_platforms` and
  `storage.tenants` — and nothing else. `service_role` has ALL on `auth.users`, `documents.*`,
  `aml.*`, `flows.*` and `storage.*`; `book_writer` has none of them, so a compromised daemon cannot
  read a client's identity documents or a firm's screening outcomes. It is not a member of
  `service_role`, so it cannot `SET ROLE` its way back.
- `BYPASSRLS` is unavoidable and is stated plainly rather than argued away: a merge writes rows for
  every tenant and `platform.*` carries `FORCE ROW LEVEL SECURITY`, which binds even a table owner.
  There is no policy expressing "all tenants" that is not equivalent to no policy.
- **`SECRETS_MASTER_KEY` still never leaves the API.** That was the proposal's real objection and it
  stands — and it turns out the pipeline needs no tenant object-store credential at all. The
  tenant-lake projection writes with the CORE credential the daemon already holds, exactly as
  `app/fanout.py` does today; the per-tenant scoped credentials exist for firm STAFF reading their
  own lake and for the API's own read path, neither of which is this.

An internal endpoint would have been a second copy of the tenant registry, refreshed on a cadence,
with its own failure mode. One narrow role reading two tables is a smaller thing to be wrong about.

## 4. The merge policy

In the database, so it is reviewable as DDL and cannot drift per provider:

| Function                        | For                                     | Identity                                          |
| ------------------------------- | --------------------------------------- | ------------------------------------------------- |
| `platform.merge_entity`         | orgs, valuations, transactions          | a natural key, named in ON CONFLICT               |
| `platform.merge_linked_entity`  | contacts, accounts, models, instruments | a `platform.links` row                            |
| `platform.merge_model_versions` | a model's allocation                    | `max(version) + 1`, on a changed set              |
| `auth.merge_provider_user`      | a custodian's own employee users        | an email that already has a login, else a new row |

Instruments are the shared catalogue: an ISIN is one row, and a firm whose fund is already in the book adopts that row. Two firms merged in the same sync take a transaction lock on that catalogue first, so the later firm adopts the row the earlier one has just written. Without the lock both passes see an empty book, and `instruments_isin_key` refuses the second insert and fails that firm's whole instrument merge.

After the accounts merge, Seccl's client product is written onto the contact. The conformed
account publishes `wrapperDetail.clientProductId` as `product_external_id`. That is one id per
tax wrapper, shared by every account of that wrapper, so the link is
`client_product:{wrapper}` on the contact (`client_product:PENSION`, `ISA`, `GIA`, `JISA`).
A later drop that names a different id updates it. SEI publishes the column as NULL and writes
no such link.

**`auth.merge_provider_user` (0123) is the one merge whose target is not a `platform.*` table, and
the only one that is SECURITY DEFINER.** A custodian's staff either ATTACH to the Graphene login
already holding the same email — exact, and exactly one of them, `aml.match_contact`'s bar — or
arrive as a disabled placeholder with an `inactive` platform membership. That membership is the
whole visibility mechanism: `auth_users_scoped_read` requires one and does NOT read its status,
where `auth.resolve_claims` grants a portal only on `status = 'active'`.

It is DEFINER because **`book_writer` deliberately holds no privilege on `auth.users`** — 0062
grants it `SELECT` on `auth.tenants` and nothing else in that schema, and 0123 does not widen that.
The function is owned by `service_role`, so the pipeline can execute exactly the statements it
performs and cannot touch any other column of a login. The consequence is one line in `merge.py`:
the staged source is a TEMP view owned by `book_writer`, so it is granted to `service_role` or the
definer cannot read its own input (`permission denied for view src_users`).

**It writes no reject ledger at all** — the only merge in the estate that files nothing. Three
reasons were tried over as many passes and each turned out to be answerable somewhere better:

- `email matches more than one login` — **the state was deleted rather than the symptom.** The
  address is stored folded (`users_email_is_folded`, 0125) and unique per tenant, so the two
  case-variant logins that made a match ambiguous cannot exist.
- `no email at the provider` — filtered by `Entity.from_sql` when the STAGING model delivers a
  tenant's slice, so in production it never reaches the merge (and a row that arrives anyway is
  skipped: it can neither attach nor insert).
- `login already mapped to a different id` — **skipped, never overwritten.** Whatever is on
  `provider_refs` is either an operator's own mapping or an earlier run's, and a nightly job is not
  the thing that should change its mind about which.

**A skip is silent here because it loses nothing**, which is the test each of these had to pass.
Every one is a statement about the CURRENT rows with both sides readable: `platform.users`
publishes the custodian's whole employee population in the firm's own slice, and
`auth.users.provider_refs` publishes what is actually mapped, so "which of this firm's staff are
not mapped, and why" is a comparison rather than a lookup. That is the reasoning that keeps
`platform.reconciliation_breaks` unbuilt — a stored copy of a derivable state goes stale the moment
either side is corrected — and here it would have gone stale nightly, re-filing the same rows for
ever until nobody read them.

**Insert the row the first time the provider shows it, then only refresh what the provider owns.**
The typed columns are Graphene's, set once from what the provider first published; the provider's
verbatim payload lives in `data` — on `links.data` for a linked entity, which is what lets one
contact exist at both SEI and Morgan Lloyd with a payload each. Two declared exceptions:
`platform.accounts.value` (the balance is the provider's to state) and `platform.transactions` in
full (Graphene never writes a transaction, so a status change is simply the truth).

An unchanged row is not written at all. That is not a micro-optimisation: `platform.links` and
`platform.nodes` carry `audit.record` triggers and the pipeline has no principal, so a merge that
rewrote every row nightly would bury the audit trail in changes that did not happen. Measured rather
than asserted: `interlace run` over an already-merged book (which forces every model to rebuild,
ignoring change detection) writes **zero** rows — `created` 0 and `updated` 0 on every entity.

## 4a. Where the ledger goes (Aug 2026)

`merge_tenant` has always returned a ledger and published it as `book_{uid}.merge_ledger`. That
model declares no strategy, so it holds ONE run, and it is reachable only from the admin SQL
console by an operator who already knows to look for a relation named after a tenant uid — so the
durable record §5 argues for had, in practice, no reader.

It is now also written to **`platform.merge_runs`** (0076): one row per entity per run, sharing a
`run_id` minted per walk, append-only by withheld privilege exactly as `merge_rejects` is. It is
read by `GET /admin/platforms/{provider}/ledger` and rendered on the platform page beside the
refused-rows card.

It is written in its OWN transaction and its failure is swallowed: the entities it describes have
already committed, so a book that merged correctly must never be reported as failed because the
record of it could not be written.

This is what separates the three states that all present as an empty book — this firm has no data
with this custodian; the chain has never run; the daemon has no `BOOK_WRITER_DSN` and therefore
registered no tenant models at all.

## 5. Rejects

`platform.merge_rejects` is append-only (no UPDATE or DELETE grant to any role, `service_role`
included). It exists because a projection with no merge leaves a firm looking empty, and the reasons
are the only thing distinguishing that from a firm with no data at this custodian. A real Seccl run
refuses 117 transactions (113 `Expectation/FAD Gross Withdrawal`, 4 UFPLS) and 2 instruments whose
ISIN is already mapped to a different Seccl asset id.

It records the source **key**, never the row: a rejected transaction is still client data and this is
not a quarantine. A reject is a statement about one run, which is why it is a table where
`platform.reconciliation_breaks` deliberately is not — a break is a statement about current rows and
goes stale the moment either side is corrected. The platform page reads the **latest** import of
each firm and entity, not the lifetime of the table: a refusal that a later import wrote is no
longer listed.

**A counted observation is not a reject, and the two have different homes.** `materialise.py` drew
that line itself — `EntityResult.reason()` is documented as "a counted observation that is NOT a
rejection" — and `Entity.observes` keeps it: a condition whose failure is worth counting but does not
stop the row. Seccl's models list the SSAS wrapper our `product_type` enum has no member for, which
does not invalidate a model that also lists four it does have, but its arrival is how we find out the
enum needs extending. So an observation goes in the **run ledger** and never in `merge_rejects`,
which an operator reads as "refused". The real run's ledger says
`{"no allocations published": 5, "unmodelled model wrapper: SSAS": 18}` against `models` — the same
two counts the old path produced, and now durable: the ledger is a warehouse relation
(`book_{uid}.merge_ledger`, per entity per run, with `created`/`updated`/`rejected`/`reasons`/`note`),
where the old path returned them in the `POST /admin/materialise/run` response and stored them
nowhere, so they survived exactly one HTTP call.

## 5a. What the deletion moved, and what it left

Deleting three modules moves whatever only they knew. Checked before, not after:

- **`provider_schema` → `app/providers.py`.** Still needed by the admin console's catalogue attach
  and by `main.py`'s `lake_reader` grant. It is `ml` for Morgan Lloyd and the bare name for the other
  two, which is exactly why it was dangerous where it sat: the coincidence held for two providers and
  hid a real bug in the third.
- **The product vocabularies moved nowhere** — `PRODUCT_TYPES`, `PRODUCT_ALIASES` and
  `PRODUCT_LABELS` were already in `book_sink.entities`, and all three were verified identical to
  materialise.py's before it went. `_ORG_TYPES` needed no home either: every provider's conformed
  model now publishes a canonical `type`, which is where that mapping belongs.
- **`providers.mark_fanout` is gone**; `book_sink.merge` stamps `last_fanout_at` itself (0066 grants
  book_writer the UPDATE), so the admin health check's "never synced" still means something.
- **The tests moved rather than died.** The SEI per-relation column fixture and the predicate's
  binding cases are now `pkgs/book-sink/tests/test_predicate.py`; the libpq/DSN cases are
  `test_config.py`. Both were learned from silent failures and neither had a counterpart on this
  side.
- **`test_pipeline_coverage.py` lost half its power, stated plainly.** It read materialise.py's
  source to prove every table it wrote was reachable from the sink. With one implementation there is
  nothing to compare, so `OWNED` is that set frozen at the deletion commit — a hand-maintained list,
  weaker than reading a second implementation. The half that still derives from the code is the other
  direction: a sink target outside `OWNED` must be named with a reason.
- **`test_pipeline_parity.py` is deleted outright.** A differential test needs two paths.

## 6. What is not done

- **Nothing of Seccl's, any more.** All sixteen conformed relations merge: the six core entities,
  the shelf and its per-platform references, models with their versions, allocations, commentary and
  per-platform references, the fee register with its references, and the two provider registers. The
  one relation that stays lake-only is `positions` — holdings are read from the lake by every surface
  that shows them, so there is no OLTP table to merge into.
- ~~**The tenant's own parquet.**~~ **Landed** as a fourth layer, `lake_{uid}.projection`: a Python
  model owning its own DuckDB connection, because interlace's `attach:` takes a plain URI and a
  DuckLake catalogue needs `DATA_PATH` + `METADATA_SCHEMA` options. It reads CORE and applies the
  predicate itself rather than reading the per-tenant slices — those are promoted at the END of an
  apply, so reading them would make this leg's correctness depend on when it happened to run. A
  core table that is not in the catalogue yet (`platform.nodes` while a virtual rebuild is waiting
  to promote) is skipped rather than failing the run: aborting before promotion is how the missing
  table persists. It needs no tenant credential (see §3), and it returns a manifest, so "did this
  firm's lake refresh, and with how many rows" is answerable from the warehouse instead of from a
  log line.
- ~~**Parity.**~~ **Two gates, both green.** `tests/test_pipeline_parity.py` is a DIFFERENTIAL test —
  one conformed fixture through BOTH paths, then the book diffed table by table modulo surrogate ids
  and resolved FKs — which is the reproducible half, and the stronger property than a row count: it
  is the only thing that can catch the two paths agreeing on how many rows and disagreeing on what is
  in them. `tests/test_pipeline_coverage.py` reads `materialise.py`'s own source and fails if a table
  the old path writes has no reachable owner in the new one.

  Then a real run, against the real Seccl book (06 Aug 2026, local, 49 models, 29/29 checks): 50
  orgs · 133 instruments · 286 Seccl contacts · 376 accounts · 13,034 transactions · 51 models · 46
  versions · 423 allocations · 666 transfer providers · 7 third-party products — every count the old
  path's capture holds. `org_id`, `product_id` and `contact_id` resolve on all 376 accounts,
  `account_id` on all 13,034 transactions, `model_id` on 311 (the figure CLAUDE.md records as
  correct — 311 of 376 accounts publish a resolvable `model_key`; a "344" in an earlier capture was
  taken before `_sync_models`' discarded return map was fixed). Valuations are 752 over two
  `as_of_date`s rather than 376, which is the series behaving as keyed: a second day adds a row, it
  does not overwrite yesterday's. `crm.contacts` is 299, being 286 Seccl plus 13 Morgan Lloyd — the
  two-providers-one-person shape `platform.links` exists for, and the reason identity does not
  resolve through a denormalised provider key.

  SEI's own first run adds the case that had never been exercised: 62 orgs · 6,170 instruments · 411
  clients · 505 accounts · 467 valuations · 3 product references, with **zero rejects**, and a forced
  re-run writing nothing. `platform.accounts` reads ISA 329 / GIA 144 / JISA 31 and 504 active to 1
  draft — both of the columns that were the standing blocker, resolving to real book rows. 24
  transaction headers exist in the whole corpus and none belong to this firm, so 0 there is data
  rather than a defect. Most of all: **90 instruments are now held by BOTH Seccl and SEI** — one book
  row per ISIN, reached from two providers, which is precisely what `0066`'s tenant-keyed links and
  `Entity.adopt_on` were built for and had only ever been proven in tests.

- **Morgan Lloyd runs, for `clients` only.** Wired and verified live (13 models, 9/9 checks): the 13
  conformed clients project into the firm's slice and merge as `created 0, updated 0, rejected 0` —
  because those people already hold `platform.links` rows minted by
  `POST /crm/contacts/{id}/sync-to-morgan-lloyd`, so the pipeline read back what we pushed and
  recognised it rather than creating 13 duplicate people. That is the round trip closing, and it is
  the clearest demonstration of why identity resolves through the link table. (The stronger claim —
  one person held at two custodians — is not evidenced by this book: ML's 13 and Seccl's 286 are
  different humans, `0` contacts hold links to both. The mechanism is pinned in `test_book_sink.py`.)

  `transfers` and `beneficiaries` conform and reach the tenant's lake but deliberately do NOT merge:
  `platform.transfers`' lifecycle is Graphene's, advanced by our own reconciliation, and a nightly
  overwrite from Dynamics could walk a transfer backwards — which is the exact thing `0056`'s
  monotonic status rule exists to stop. The rest of ML's scope (schemes, contributions, benefits, bank
  transactions, advisers) needs conformed models over the `cam_*` OData collections first.

- ~~**SEI is un-registered, on two columns.**~~ **Both answered from a real drop, and SEI is
  registered** for six relations (orgs, instruments, clients, accounts, valuations, transactions).
  The answers, because neither was guessable from the column names:
  - **`product_key` is `ACCOUNT_TYPE`, not `PORTFOLIO_TYPE`.** The Reference report's `ACCT_TYPE`
    decodes the codes in play — 157 Investment Account → GIA, 172 Stocks and Shares ISA → ISA, 173
    Stocks and Shares Junior ISA → JISA — while `PORTFOLIO_TYPE` is 189 Capital Portfolio / 190
    Income Portfolio, an investment-purpose split with no bearing on the tax wrapper. Mapped on the
    CODE, which is the stable identifier and carries a blank `FIRM_ID`, so it is platform-wide (the
    property that let the contribution treatment codes be pinned). 176 House Account is deliberately
    unmapped: it is the firm's own account, not a client product, and `product_id` is not a required
    parent, so it keeps a NULL instead of being filed as something it is not.
  - **`status` maps `ACCT_STATUS`** — 1 Pre-Open, 3 Open, 5 Pre-Close, 6 Closed, 8/9/10 Work In
    Progress — onto draft | active | closed. **Pre-Close is active**, the one judgement rather than a
    lookup: the account is winding down but still exists and still holds assets, and SEI has a
    distinct terminal code for when it does not, so calling it closed would hide 192 live accounts
    from every screen that filters on status. `status_raw` keeps SEI's own code beside it.
  - **This environment defines no pension WRAPPER** — `ACCT_TYPE` is exactly those four codes — but
    it is full of pensions all the same, carried in a different code set: 934 accounts are
    `ACCOUNT_CLASSIFICATION_NAME` Pension and 830 are `Pension - HMRC Approved - SIPP`, every one of
    them on wrapper 157. So `product_key` reads the classification FIRST; mapping on the wrapper
    alone filed all 830 SIPPs as GIAs, which is what every "is this a pension?" reader gates on.
    Worth knowing before planning anything pension-shaped on SEI: an unmapped code arriving later
    lands as NULL and shows up as `unmodelled wrapper` in the ledger rather than being coerced.

  `test_book_contract.py`'s `KNOWN_GAPS` stays as the live table, still checked in both directions: a
  gap that WIDENS is a conformed model that stopped publishing something, which would otherwise
  surface only as a tenant's slice going quietly missing.

- **A row's org comes from its parent, and `[]` is not "unknown" (Sept 2026).** `platform.accounts`
  resolves `key`/`lineage` once for everything account-grained and `platform.clients` for the client
  axis; the children SELECT them rather than recomputing from the provider's tenancy dialect, so a
  fact and the account it is about cannot disagree about which node owns it. And an unresolvable
  owner publishes **NULL**, never `[]` — `[]` is `predicate.conformed`'s GLOBAL sentinel, so twenty-one
  models wrapping a LEFT JOIN miss in `coalesce(…, [])` would have published one bad node's account,
  positions, transactions and valuations into EVERY enabled tenant's slice. Live for Seccl and ML,
  dormant for SEI until the switch below. `pkgs/book-sink/tests/test_lineage_sentinel.py` scans all
  three providers' models and names the relations that genuinely are global.
- **SEI keeps `predicate.sei()`**, not `lineage` containment. Its enablement is the
  firm/subfirm/DSR triple, and containment tests ancestry — so firm AND subfirm AND team would become
  OR, widening any multi-axis slice. That switch waits on migrating `tenant_platforms` config to leaf
  org keys, which is the same widening the general fix defers.
- **No `models` or `fee_packages` for SEI.** It has both concepts, but the strategy standing in for a
  model is published as `model_key` on the account (resolved through the link), and the fee register
  lives in reports this layer has not conformed. Registering a relation the provider does not publish
  is a Binder Error that aborts the whole tenant, so the register is deliberately short.
- ~~**The withdrawal gate still reads a committed fixture.**~~ **Moved (Aug 2026).**
  `instructions.py`'s available-money check now reads the firm's own slice —
  `tenant_{uid}.positions`, through `app/lake/holdings.py` — instead of `docs/seccl.duckdb`, an
  anonymised, downsampled extract committed to this repository and read as THE positions for every
  firm in every deployment. Both ways of being wrong were silent: an account id absent from the
  extract refused every withdrawal on that account for ever, and an id present in it (the extract
  keeps real Seccl ids) decided a cash-out against someone else's anonymised numbers.

  Three things the move had to settle, none of them guessable from the column names:

  - **Cash is the vendor's word.** `platform.positions` publishes no `asset_type`; it publishes
    `position_type`, which is Seccl's own `positionType` (`Cash` | `Stock`, and a cash position
    carries no asset fields at all). It is mapped into the `asset_type` slot
    `instructions.is_cash_holding` already reads, so the ONE definition of what counts as cash is
    unchanged — including the money-market funds that are cash by NAME rather than by type, which
    is the rule that stops a fund manager's naming choice inflating a withdrawable balance.
  - **"Could not read" now RAISES where "holds nothing" returns empty.** The fixture reader answers
    `[]` for a missing file, so a money control saw the same value for an absent warehouse and an
    empty account. Both still refuse — for a cash-out, "we can't tell" must never mean "go ahead" —
    but they say different things, and only one is worth paging somebody about.
  - **The schema is the narrowing**, so there is no predicate here to get wrong, and the per-firm
    console and the money control cannot disagree about what a firm's slice is.

  **The display reads followed, and the fixture reader is gone.** `_lake_metrics`, the holdings
  page, the recent-activity feed and the value chart all read the firm's own slice now, and
  `app/lake/seccl.py` — the module that opened `docs/seccl.duckdb` — has no callers left and has
  been deleted with its `LAKE_SECCL_PATH` setting. The extract stays in the repository as a
  reference for Seccl's shape; `tests/test_lake_holdings.py` fails any module that reaches for it
  again, scanning string LITERALS through the AST so that explaining what it replaced does not
  trip its own tripwire.

  The series mapped more cleanly than expected: the fixture derived it by SUMMING asset valuations
  per account per day, and `platform.valuations` publishes the account's own `market_value` per
  `as_of_date` — a published figure rather than a re-derived one. Transactions needed two
  decisions: `asset_name` is on the INSTRUMENT (one name per instrument, not one per movement), so
  it is a LEFT JOIN that costs a label rather than a row; and `movement_type` has no conformed
  equivalent, so the canonical `type` goes in that slot — `crm._activity_type` and `_signed` match
  needles ("div", "fee", "withdraw", "sell", "buy") against those three fields concatenated, and
  the canonical vocabulary IS those needles, so the classifier gets a better signal than the one
  it lost.

  These DEGRADE where the gate refuses: `_firm_read` logs an unreadable firm and returns what it
  could read, because a partial page beside an authoritative OLTP `value` is a better answer than
  no page — the opposite of the call `instructions.py` makes about the same warehouse.

- **Incrementality.** Every projection is still a full refresh. Cheap at 13k transactions, not at 50
  firms; the conformed layer carries `source_updated_at`, so this is a later change with the parity
  test already in place.

## 6a. How a firm gets a provider — the enablement path

Three registries, and they are not interchangeable:

| Thing                        | Where                                           | Written by                                                        |
| ---------------------------- | ----------------------------------------------- | ----------------------------------------------------------------- |
| Which providers exist        | `app/providers.py::PROVIDERS` + `provider_code` | CODE ONLY — there is no table (0131)                              |
| How a provider PRESENTS      | `app/providers.py::PROVIDER_META`               | code, served on every platform payload as `meta`                  |
| A firm's connection + config | `platform.tenant_platforms`                     | `PUT /admin/tenants/{id}/providers/{provider}` (`tenants:write`)  |
| The filter's allowed values  | the WAREHOUSE, live                             | nobody — `GET /admin/providers/{provider}/options` reads the lake |

**`platform.providers` is gone too (0133).** It was one row per named upstream CONNECTION, and it
held Morgan Lloyd rows and nothing else — deliberately, since Seccl's and SEI's connection is the
deployment's credentials. ML's connection is one Microsoft tenant per firm, so its join to the
enablement was always 1:1, and `resolve_source_config` existed only to walk it and REFUSE when a
firm held two; that refusal guarded a shape nothing could act on. The vendor reference values
(`firm_id`, `adviser_contact_id`, `scheme_type`, `sipp_type`) sit on the firm's own
`tenant_platforms.config` beside the slice filter now, as optional keys `validate_config`
shape-checks.

The consequence is the point rather than a side effect: **registering a connection and enabling a
firm on it are ONE STEP.** There is no registry to pick from, so `providers.list_sources`/
`set_source`, `GET`/`PUT /admin/providers/{provider}/sources` and the console's "Register Morgan
Lloyd Tenant" sheet are all gone — and with them the one write that could not be given
`morgan-lloyd`.

**`platform.platforms` is gone (0131).** It was the first row of that table and it held nothing a
provider code does not: it existed to be a foreign-key target, so eight columns paid for a join to
learn a value they could have carried. `platform.provider_code` is the enum those columns hold now,
and `sync_platforms()` — which seeded the table on startup, and whose absence rejected every
product for every provider — has nothing left to do. **One spelling too**: `morgan-lloyd`
everywhere, so the slug/key bridge (`PROVIDER_META['slug']`, `by_slug()`) is deleted rather than
mended. `provider_schema()` keeps `ml` for the warehouse until that schema is renamed, because it
is a physical object rather than an identifier.

**`PROVIDER_META` is new, and it is a consolidation rather than an addition.** Name, role,
description, transports, the enablement picker's caption and the KPI relations lived in the ADMIN
APP, in seven hard-coded tables over the same three custodians — `LIVE_PROVIDERS`, `LIVE_META`,
three fixture `meta` blocks, `PROVIDER_DEFS`, `PROVIDER_NAMES`, `PLATFORM_NAMES` and a second
`PROVIDERS` tuple. None derived from the API, and they had already drifted: Seccl was described
three different ways depending on which page you were on. Adding a fourth custodian meant finding
all seven. It is one edit now, and `_known_provider` resolves a URL slug as well as a provider key,
so the app holds no slug map either.

The rule that mattered was the FALLBACK. An unlisted provider used to resolve to
`LIVE_META.morgan_lloyd`, so every SEI page carried Morgan Lloyd's name and description above SEI's
own data and its own "Send to…" list — the confusion that gets an instruction sent to the wrong
custodian. `providers.meta()` owns unnamed-beats-misnamed now, once, server-side.

**A firm can see its own data path** (`GET /firm/data-sources`, platform portal → Data sources,
`data_sources:read`). Which custodians feed its book, when data last landed, and how much did not
— scoped server-side from the caller's identity with no tenant parameter, the `/firm/audit/actions`
posture. Read-only with no write sibling: the enablement and its slice keys are the custodian's own
identifiers and stay Graphene's to set, so a control there would be one the firm cannot use. It
withholds the slice keys for the same reason.

**`platform.providers` is Morgan Lloyd's alone, and that is deliberate.** ML's slice key is the
connection's Microsoft tenant id, and there is nothing to discover in the lake — the tenant id being
enabled is exactly the one the sync has never seen — so its picker reads the registry. Seccl's nodes
and SEI's firms/subfirms/DSR teams are discovered from the conformed layer instead, which is why
`platform.providers` holding no Seccl or SEI rows is correct rather than a gap.

The corollary is the failure mode: **a picker with an unbuilt model degrades silently to manual id
entry**, by design (`_options_sync` returns an empty list for a relation that is not promoted, because
a provider can be configured before its models have ever run). SEI's DSR list read 0 of 503 for
exactly that reason — `platform.dsr_teams` had not been built — and an operator would have seen an
empty dropdown, not an error.

The filter shape is validated per provider (`providers.validate_config`): Seccl `node_ids`, ML
`tenant_ids`, SEI `firm_id` required with `subfirm_ids`/`dsr_ids` optional (absent = the whole firm).

**Enabling triggers the chain**: `_run_tenant_pipeline` asks the provider's daemon to run this
firm's models. It is reported rather than raised, and it cannot fail the enablement, which by then
is already saved — a 503 would tell the operator the enablement failed when it did not.
The daemon recompiles when the enabled-firm set changes (`book_sink.tenant_reload`), so a firm
just attached is in the graph on this request. `restart_required` is still set when the run
selects nothing. A tag that matches no models is a 400 from the daemon — no active bucket, or
the firm is not enabled — and that detail is what the operator sees.

(This paragraph used to say enabling triggered BOTH paths "because both are live until the old one
is deleted". The old one was deleted in the same change that wrote §1; the sentence outlived it.)

## 7. What it needs configured

| Variable                     | Where                           | Why there                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ---------------------------- | ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `BOOK_WRITER_DSN`            | root `/.env` + `x-warehouse`    | libpq keyword form, `book_writer` on the portals DB. Shared, because all three provider services need it and each reads `/.env` before its own — the rule that file exists to keep. Unset, interlace keeps the literal `${BOOK_WRITER_DSN}` and fails on connect, and `book_sink` registers no tenant models (logging why, since an unset DSN otherwise reads as a daemon with no tenants). **It was absent from the production compose entirely** — there is no `env_file:` there, every service enumerates its own `environment:`, and the shared `x-warehouse` anchor omitted it. So the merge was inert in production for every provider while every daemon reported healthy. It is in `x-warehouse` now, which is exactly the block that exists to hold it. Local compose hardcodes `host=db` rather than interpolating `/.env` (`localhost`), because inside a container that host is the container itself. |
| `DATALAKE_WRITER_DSN`        | root `/.env`                    | already there; the tenant-lake projection opens both catalogues through it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `INTERLACE_{PROVIDER}_URL`   | the API's env                   | **All three.** An unset one is invisible in the worst way: `platforms.service_url` returns `""`, the provider reports `service: unconfigured`, its pages still render, and every sync / refresh / rebuild for it answers 503. SEI and Morgan Lloyd were both unset AND had no container defined at all, so two of the three custodians could not run in a deployment. `/health/ready` now carries a `pipelines` probe that names an unconfigured provider separately from an unreachable one — two different problems with two different fixes.                                                                                                                                                                                                                                                                                                                                                                   |
| `S3_*`                       | root `/.env`                    | already there; the projection writes the tenant's parquet with the CORE credential (§3).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `attach: book:`              | each service's `interlace.yaml` | the alias the staging models deliver into. It travels with `models/tenants.py`, and a registered provider missing either is registered-but-absent — which fails silently, because an absent model is not an error and a tenant tag matching nothing is a 400, not a silent success. ML's test pins both.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `book-sink` as a RUNTIME dep | each service's `pyproject.toml` | `models/tenants.py` imports it at module load and interlace discovers models by importing them — so a `--no-dev` install (what every Dockerfile does) silently loses the whole per-tenant chain. SEI and ML both had it under `dev`, with a comment saying the service did not import it at run time, which was true before the chain landed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |

Nothing goes in a service's own `.env`: those hold that provider's API credentials and nothing else.

**All three services are deployable.** `services/{sei,morgan-lloyd}/Dockerfile` were missing
entirely and `services/seccl/Dockerfile` could not build — it copied `pkgs/api-log` alone, while
that service's `[tool.uv.sources]` had grown `book-sink` and `seccl`, so `uv sync --frozen` could
not resolve two of the three. A Dockerfile's COPY list has to be kept in step with the member's
workspace sources by hand; nothing else notices, because every other gate passes.

## 8. Operating it

- **All three daemons start with `python -m book_sink.startup_run`.** That is `interlace serve --no-apply --allow-open`, then one full `POST /runs {}` once `/health` reports `ok`. Interlace 2.8 refuses a non-loopback bind when the state database has no API keys, and that exit is what marks the container unhealthy. The pipelines are reachable only on the compose network. Each model's `schedule={"cron": …}` is compiled into the scheduler (on by default, 60s tick). That is the nightly/weekday cadence. From interlace 2.8.3 a scheduled fire runs that model and everything downstream of it; ancestors are not pulled in, and a `POST /runs` selector stays exact. A cron slot still waits for its clock. The boot path does not apply: a breaking plan refuses the whole startup apply and leaves the previous warehouse in place, and a run has no such gate, so a definition change is picked up by that full run. A run already in flight, or a succeeded run inside `SYNC_ON_START_DEBOUNCE_SECONDS` (default 30 minutes), is not enqueued again; `SYNC_ON_START=0` turns the kick off. From 2.8.8 a schema-qualified DuckLake create stays in that schema; before it, a parallel build could commit the table into `main` and the next model failed to find it. Health reports `ok` as soon as the process is listening, because the boot apply is off; the full run does not hold writes. `api:db-reset` deletes the host `.interlace` state with the volumes; a daemon left running through the reset still holds the old file until restart. A manual sync is `POST /runs`, not `interlace apply`.
- **A new tenant is picked up on the next request.** Enablement is a row, not a model file, so
  interlace's own reload (which watches `.py` / `.sql` mtimes) does not see it. Each daemon's
  `models/tenants.py` wraps that reload: when the book's enabled firms differ from the compiled
  `tenant:` tags, it recompiles before the request selects models. Refresh of a firm just attached
  therefore runs that firm's chain. A tag that still matches nothing is a 400 (`tag … matches no
models`) — the firm has no active bucket, or it is not enabled — not an empty success.
- **Selectors.** Every model for one firm is tagged `tenant:{uid}`, so
  `POST /runs {"selectors": ["tag:tenant:{uid}"]}` refreshes exactly that firm.
- **The admin platform pages follow the daemon live.** `GET /events/stream` is server-sent
  events (comment frames, `id` = sequence). The browser cannot send the API bearer, so it opens
  `EventSource` on `/platforms/{slug}/events` and that route copies
  `GET /admin/platforms/{provider}/events/stream` (`platforms:read`, no database session — the
  connection stays open). `?after=` is the last sequence already applied, so a reconnect does not
  replay the log. A finished run still reloads the overview: row counts are not on the event.
  Cancel is `POST /runs/{id}/cancel`. Rebuild is `POST /reset` with `confirm: true`, then a full
  run — see `docs/datalake.md`. The reset drops the `streams` landing schema; the daemon
  recreates those tables before the run is enqueued, so the overview's stream list survives it.
  The overview waits up to a minute for the daemon's lists, and a slow plan does not by itself
  mark the service down. Rebuild waits up to five minutes for `POST /reset` to answer: the
  previous five-second budget abandoned the wipe, so the button looked as if it had done
  nothing while the event stream (no read deadline) still said live updates were connected.
- **Reset pause.** `platform.tenant_platforms.refresh_paused_at` is an operational gate, not an
  enablement setting. Tenant model discovery excludes paused rows, and already-loaded models
  check it again at execution time before filtering, merging or projecting. The Operations data
  tool sets it before queueing a reset and only an explicit Resume clears it; provider config and
  `enabled` are left unchanged.
- **One transaction per entity, not per tenant.** A nightly batch that discarded a firm's accounts
  because one transaction carried an unmapped type would be a worse outcome than a partial landing
  the ledger names. The entities are ordered, so what committed is a prefix and the next run resumes.
