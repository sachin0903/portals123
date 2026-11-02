# morgan-lloyd — CRM sync on interlace v2

Pulls the Morgan Lloyd (Clifton Asset Management) book out of their Dynamics
365 CRM into the warehouse. Unlike Seccl there is no webhook surface and no
updated-since filters, so this service is **poll-only**:

```
MorganLloydClient (pkgs/morgan-lloyd)
        │  cron pulls
raw.* extractors ──full_merge / full──▶ warehouse ──▶ platform.* views
```

- **Whole-book listings** (`models/raw_core.py`): contacts, transfers
  (cam_personalpolicies), beneficiaries, employer accounts, providers. A full
  pull is unavoidable API-side, but it lands as a SQL set-difference diff
  (`full_merge`) — only new/changed/vanished rows are written. Models live in
  the `raw` schema (`raw.contacts`, `raw.transfers`, …). An empty listing is
  the full state, so `full_merge` deletes the stored rows.
- **Scoped pulls** (`models/raw_scoped.py`): the CRM cannot enumerate schemes
  or advisers, so contribution applications, received contributions and scheme
  bank transactions fan out over `ML_SYNC_SCHEME_REFS`, and adviser books over
  `ML_SYNC_ADVISER_EMAILS` (comma-separated; blank ⇒ the model lands an empty
  table). These use plain `full` replacement — safe for empty scopes.
- **Platform layer** (`models/platform/`): thin current-state views; `contacts` overlays
  the adviser-book pull for contacts not visible in the main listing.

Connection/auth env is owned by the client package (`MORGAN_LLOYD_*`, see
`pkgs/morgan-lloyd`); sync scopes are `ML_SYNC_*`. Copy `.env.example` to
`.env` and fill in the credentials issued by Morgan Lloyd.

## Run

```
moon run morgan-lloyd-sync:dev        # interlace API + scheduler on :8102
moon run morgan-lloyd-sync:plan       # show what a run would do
moon run morgan-lloyd-sync:run        # one-shot run of due models
moon run morgan-lloyd-sync:populate   # seed the DEV CRM with deterministic test data
moon run morgan-lloyd-sync:illustrations  # CTC1 XML + PDFs + drawdown SSO links
moon run morgan-lloyd-sync:lint morgan-lloyd-sync:typecheck morgan-lloyd-sync:test
```

`scripts/populate_test_data.py` writes tagged test records
(`cam_externalclientid = GRAPHENE-TEST-*`, `ml-test-*@example.grapheneplatforms.com`)
through the real API: contacts → schemes → transfers → beneficiaries →
contributions → ATRs. It is deterministic (`--seed`), idempotent (skips
contacts whose email already exists; `--resume-existing` repairs a partial
run) and supports `--dry-run` to print the payloads without touching the CRM.
Point it at production credentials and you will create junk in a real book —
it is wired for the dev environment.

Create Scheme uses the values Morgan Lloyd issued us (schemeType `16`,
sippType `809130016`; `--probe` walks all documented pairings instead), and
Create Investment uses the issued provider firm ID. **Scheme creation is
currently blocked**: every valid pairing 403s on an ML-Team field-secured
column for our API user — Morgan Lloyd (crmsupport@clifton-asset.co.uk) must
grant the app user create rights on the Scheme entity's secured fields before
schemes/beneficiaries/transfers/investments/contributions can seed. ATRs are
skipped when the contact record exposes no int Client Reference Number field.

`scripts/generate_illustrations.py` builds a **CTC Bespoke XML v1.6** drawdown
illustration per test contact and, for each one:

1. POSTs the XML to **CTC's calculation engine**
   (`https://cam.ctc.uk.com/Pages/Remote.aspx?wci=RunQuote&nodb=true` — access
   is IP-whitelisted; ours works) and saves the returned key-facts PDF into
   `.data/illustrations/` (fallback: a watermarked local preview,
   `<ref>.preview.pdf`);
2. creates a benefit application (`cam_setmemberdetails`) carrying the XML as
   its `illustrationTemplate` — the printed `ssoUrl` (CTC DCube,
   `digitaltest.ctc.uk.com`) opens the member drawdown journey.

`--skip-crm` renders XML + preview PDFs offline; `--no-ctc` skips the engine.
Note the drawdown journey's write-back (Drawdown Setup, submitted documents)
attaches to the **scheme member**, so until scheme creation is unblocked the
journey outcome is not visible via `cam_memberdetails`/`cam_schemememberevents`.

Vendor reference documentation (workflows, API catalogue, CTC XML map, the
API-details docx and workflow spreadsheets) lives in `docs/morgan-lloyd/` at
the repo root.
