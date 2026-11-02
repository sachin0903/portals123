# Object storage — core + bucket-per-tenant

The reference for all object storage on the platform: what buckets exist, what lives
where, how tenant buckets are provisioned, and how files are accessed. Companion to
`docs/data-architecture.md` (the wider data model) and `docs/datalake.md` (the analytical
lake built on these buckets).

Engines: S3-compatible object storage (RustFS locally, any S3-compatible store in
production) · DuckDB / DuckLake for the analytical lake (catalogues in the `datalakes`
Postgres database) · the FastAPI backend as the only writer of tenant files.

---

## 1. Topology — two kinds of bucket

| Bucket                       | Who can reach it                                           | Purpose                                                                                                                               |
| ---------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `platforms` (one)            | Graphene only — **no tenant credential can ever reach it** | Pre-resolution data from the PLATFORMS we integrate with (Seccl, SEI, Morgan Lloyd): raw drops, whole-firm staged parquet, quarantine |
| `integrations` (one)         | Graphene only                                              | The same for non-platform integrations (ComplyCube, CTC, Postcoder, Resend) — see below                                               |
| `tnt-{uid}` (one per tenant) | That tenant's four scoped service users + the platform     | Everything that is that tenant's: lake, files, exports                                                                                |

**Why `integrations` is separate from `platforms`.** Different blast radii: the credential that
runs a provider ETL should not reach an identity-screening artefact. It also keeps the naming
honest — `platforms` is named for the platforms, and an AML document in it would be neither.

It **starts empty**, which is not an oversight. None of those four persists a file there today:
ComplyCube results live in Postgres (`compliance.*`); a portal client's identity photos never
reach object storage (they are held sealed in `compliance.identity_photos` only until the identity
step finishes, and the console reads them from ComplyCube), and a partner firm's go to the firm's
own document bucket. CTC returns a PDF the API files as an ordinary tenant document, Postcoder and
Resend hold nothing. The bucket exists so the next integration has
an obvious home rather than borrowing the provider bucket and quietly becoming provider data.

The **per-tenant bucket is the sovereignty boundary**. Every object that belongs to a tenant —
lake parquet, client documents, generated reports, branding — lives in that tenant's bucket and
nowhere else (the core bucket holds only _pre-attribution_ provider data). Offboarding a firm =
export/hand over the bucket. Re-homing a tenant (local RustFS → hosted S3) is a registry update,
not a data migration, because stored keys are bucket-relative (see [§5](#5-keys-are-bucket-relative)).

Bucket names use the immutable tenant uid's hex (`auth.tenants.uid` without its `ten_`
prefix — bucket names can't contain underscores), never the alias (aliases can be renamed;
buckets cannot) and never the serial id (uids are unguessable and stable across systems). The `tnt-` prefix is configurable (`S3_TENANT_BUCKET_PREFIX`)
because AWS bucket names are globally unique — hosted deployments use e.g. `graphene-prod-tnt-`.

### Why raw lands core-side

Provider batches are **whole-firm and multi-tenant** (an SEI DSR file mixes tenants; which
Graphene tenant a row belongs to is only known _after_ membership unpivot + tenant-rule
resolution, and some rows resolve to _no_ tenant → quarantine). So raw and staged data
land in `platforms`, and only **post-resolution, grant-filtered** data fans out into
per-tenant buckets. The same grants that drive RLS drive the lake fan-out, so OLTP and
lake can never disagree about ownership.

---

## 2. Core bucket layout — `platforms`

The bucket is named for what it holds: the **platforms** we integrate with. It is one
bucket per deployment (dev / staging / production each get their own object-store tenant),
which is why nothing in it carries an environment segment — two environments cannot collide
inside a bucket only one of them can reach.

```
{provider}/{zone}/{feed}/dt={YYYY-MM-DD}/{object}
                                    # provider-first: SEI's drop lands here
                                    #   sei/sftp/{report}/dt=…/{name}.out.pgp   as sent
                                    #   sei/staged/{report}/dt=…/{name}.out     decrypted
raw/{provider}/{feed}/dt={YYYY-MM-DD}/run={sync_job_id}/{object}
                                    # zone-first: as-received, write-once (Seccl file sync)
raw/{provider}/webhooks/dt={YYYY-MM-DD}/{external_event_id}.json
                                    # verified webhook bodies
datalake/{provider}/…              # typed whole-firm parquet (the provider DuckLake catalogues)
files/{provider}/…                 # provider-sourced FILES kept core-side (pre-resolution): the
                                    # counterpart to datalake/, and what a tenant bucket mirrors
                                    # under files/platforms/ once a slice is attributed
quarantine/{provider}/{feed}/dt={YYYY-MM-DD}/…
                                    # rows that resolve to no tenant
config/                             # connector fixtures, reference-data snapshots
```

Two shapes, honestly: **provider-first** (`sei/sftp/…`) is where new provider zones go, and
SEI is the one that has moved. **zone-first** (`raw/{provider}/…`) is the original convention
and still what Seccl's file sync writes. They are not merged retroactively because the
migration cost is real and buys nothing — a reader is given its prefix, not asked to derive
it.

Invariants:

- **Write-once raw.** Object keys embed provider + feed + date (+ run id, on the zone-first
  shape), giving lineage back to the sync. Raw objects are never overwritten (versioning +
  deny-overwrite policy in production).
- **No environment segment.** The bucket is per environment; see above.
- **Idempotency key** for anything landed from a provider: `(provider, external_ref)`.
- `{provider}` ∈ `seccl | sei | ml | …`; `{feed}` is the provider's own feed/extract name.

## 3. Tenant bucket layout — `tnt-{uid}`

```
datalake/                          # DuckLake data; catalogue schema = tenant uid in Postgres
  seccl/…                           #   silver: tenant-filtered source mirrors
  sei/…                             #   (Seccl filtered by node lineage; SEI by firm_id +
  ml/…                              #    resolved DSR grants; ML by scheme/adviser scope)
  marts/…                           #   gold: conformed marts the portals and reports read
  archive/{events|audit}/…          #   immutable event/audit parquet (WORM, FCA 6-year)
files/
  tenant/…                          # branding/CMS images — browser access is API-proxied
  documents/{contact_uid}/{document_uid}/v{n}/{filename}
                                    # verified client documents, incl. identity documents
  reports/{YYYY}/{run_uid}/…        # generated: illustrations, valuations, fact sheets
  provider/{provider}/{feed}/dt={YYYY-MM-DD}/…
                                    # tenant copies of provider files (Seccl contract
                                    # notes/statements, SEI extracts, ML SFTP documents)
  shared/…                          # staff sharing area — the one prefix user credentials
                                    # may write directly (storage:shared)
  uploads/{contact_uid}/…           # client self-uploads: pre-verification landing zone
exports/{YYYY-MM-DD}/…              # sovereignty / offboarding exports
```

Documents, branding, attachments and rendered reports are all written **here** — the API resolves
the bucket per tenant (`tenant_storage.document_bucket`) and records it on the file row, so a
tenant provisioned today writes into its own bucket while everything filed before that keeps
resolving from the shared legacy `S3_DOCUMENTS_BUCKET`. There is no migration and no cutover:
`documents.files` has carried a NOT NULL `bucket` with `UNIQUE (bucket, storage_key)` since 0005,
and the read path now honours it.

Tenant keys carry **no tenant segment** — the bucket is the tenant, so repeating it would make
every key longer than the thing it identifies. The corollary matters: the bucket is **not
derivable from the key**, so a reader must take it from the row. A read pointed at the wrong
bucket does not degrade, it 404s, and a 404 reads as "the document isn't there".

Write discipline (enforced by credentials, not convention, once per-tenant service users are
live — see [§7](#7-provisioning)):

- **`files/` is written only by the API** (upload and generation endpoints), acting with
  platform credentials after its own authorisation checks — with one exception:
  `files/shared/`, writable directly by user credentials carrying `storage:shared`.
- **`datalake/` is written only by the ETL** (the fan-out from `platforms/datalake/`), as the
  single writer of the tenant's DuckLake catalogue.
- **WORM/object-lock** on `files/documents/` and `datalake/archive/` in production — regulated
  records (FCA 6-year horizon); GDPR erasure is PII-redaction-within-retained-records.

### Documents taxonomy

Who puts what where:

| Actor                        | Flow                                                      | Location                                                                                                                                                |
| ---------------------------- | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Client                       | self-upload (statements, proofs, correspondence)          | `files/uploads/{contact_uid}/…` → promoted to `files/documents/…` once verified                                                                         |
| Client / adviser             | identity documents (passport, proof of address)           | `files/documents/…`, flagged as identity in the future `documents.files` domain table; referenced by `compliance.identity_verifications` / `aml_checks` |
| Adviser                      | uploads for a client; generated illustrations, valuations | uploads → `files/documents/…`; generated output → `files/reports/{YYYY}/{run_uid}/…`                                                                    |
| Graphene admin / ops         | fact sheets, valuations, ad-hoc documents                 | `files/reports/…` (client-facing) or `files/tenant/…` (firm-level, public)                                                                              |
| Platforms (Seccl / SEI / ML) | contract notes, statements, SFTP document packs           | `files/provider/{provider}/{feed}/dt=…/…` (tenant copies, written by the fan-out)                                                                       |

The `documents.*` schema (`docs/proposed-schema.sql`) is the domain model over these objects:
`documents.files` rows hold the **bucket-relative** `storage_key`, content type, size and
sha256; `documents.documents` links files to accounts/contacts and the generation pipeline.
Uploads get a virus scan before promotion out of `uploads/` (project-context §6). Not built
yet — this spec fixes where its objects live so nothing has to move later.

## 4. Public vs authenticated access

- **Browser-public through the portals: `files/tenant/*`** (branding and CMS images, including
  the logged-out login page). The object store itself may be internal-only. Portals serve immutable
  `/_assets/{opaque-name}` URLs and relay them server-to-server through
  `GET /internal/assets/{name}`; endpoint, bucket and storage key never reach browser JSON or HTML.
  The host resolves the tenant, so the same path cannot select another firm's bucket.
- **Everything else is private.** Access is either:
  - **API-mediated** — the download endpoint enforces the same tenant + ownership scope as
    the DB (RLS), then streams or issues a **short-lived presigned URL**. Document download is
    a classic cross-tenant leak (separate code path from RLS), so this endpoint is treated as
    security-critical. Never hand a tenant a static key to their bucket for interactive use.
  - **Tenant-credentialed** — the ETL and the lake attaches (`app/lake`) use the tenant's
    own scoped service credential, which physically cannot reach any other bucket.
  - **User-credentialed** — see below.

### Per-user credentials

Platform-portal staff can hold personal S3 credentials for their firm's bucket
(`/storage` API + the portal's Account → Storage page; `app/storage_credentials.py`).
Static keys, minted as literal per-user IAM users on the store (`svc-u-{tenant}-{hex}`,
policy scoped to exactly that bucket), governed by the `storage:*` permission family:

| Permission                | Grants                                            |
| ------------------------- | ------------------------------------------------- |
| `storage:read`            | read `files/reports/` + `exports/`                |
| `storage:read_all`        | read all of `files/` + `exports/` — never `lake/` |
| `storage:shared`          | read **and write** `files/shared/`                |
| `storage:credentials_own` | issue/rotate/revoke one's own credential          |
| `storage:credentials`     | issue/list/revoke for any member of the firm      |

Rules: scope is computed from the holder's permissions **at issue time** (RBAC changes
require a rotate); the secret is shown **once** at issue (stored only Fernet-encrypted in
the service-role-only `storage.user_credential_secrets`, for re-pushing store IAM state);
one active credential per (tenant, user) — re-issue rotates, revocation deletes the store
user so access dies immediately. Every issue/rotate/revoke lands in the audit trail. The
`/storage` endpoints are tenant-target-aware: firm staff default to their own firm, and an
operations principal (whose own tenant is the core Graphene tenant) can target any tenant
with `?tenant=<uid>` under `tenants:read`/`tenants:write`.

## 5. Keys are bucket-relative

Stored storage keys (e.g. the future `documents.files.storage_key`) are **relative to the
tenant bucket** — `files/documents/…`, never a full URL and never bucket-qualified. Bucket +
endpoint resolve at request time from the registry (`storage.tenants`), so moving
a tenant between stores is a registry update. Uniqueness is per tenant:
`UNIQUE (tenant_id, storage_key)`.

**Exception:** branding fields inside `auth.tenants.theme` retain their compatibility names
(`logo_url`, etc.) but uploaded values are same-origin `/_assets/…` sources, never object-store
URLs. Legacy absolute storage URLs are translated on read, so re-homing changes only the registry.

## 6. The registry — `storage.tenants`

One row per tenant (migration `0027_tenant_storage`, which also creates the `storage`
schema — the object-storage plane; the physical file registry `storage.files` lands here
later with the documents work, while `integrations` stays connectivity-only per
data-architecture Decision 7):

- `kind` (`rustfs | s3 | minio`), `endpoint`, `region`, `bucket` (unique) — where the tenant's
  storage actually is. Local dev: RustFS at `http://localhost:9000`; production: the managed
  store.
- `public_base_url` — the base for public branding URLs. **Set only after the
  `files/tenant/*` anonymous-read policy has been applied successfully**; while NULL, branding
  uploads fall back to the legacy shared `branding` bucket (see [§8](#8-branding-migration)).
- `access_key_id` + Fernet-encrypted secret in `storage.tenant_secrets`
  (service-role-only table, mirroring `auth.environment_secrets`) — the tenant's scoped
  service user, when the backend supports minting one (nullable: best-effort, see below).
- `status`: `provisioning → active`, `suspended` when the tenant is disabled.

RLS: a firm can read its own row (`tenant_id = ANY (auth.tenant_lineage())`); all writes are
service-role-only. The secrets table has no authenticated grant at all.

## 7. Provisioning

Creating a tenant (`POST /admin/tenants` → `provisioning.provision_tenant`) automatically:

1. Upserts the registry row (`status='provisioning'`, bucket `tnt-{uid}`, endpoint/kind from
   the instance's `S3_*` settings).
2. Creates the bucket (plain S3 API, platform root credential — idempotent).
3. Applies the anonymous-read bucket policy for `files/tenant/*`; on success sets
   `public_base_url`.
4. Best-effort: mints **four** scoped service users (`0061`) and stores each credential
   (Fernet) in `storage.tenant_service_users` + `…_secrets`, keyed `(tenant_id, role)`.
   Backends without a working IAM API skip this (no rows; consumers fall back to the
   platform root credential).
5. Marks the row `active`.

### 7.1 The four service users

One credential per consumer, each with the narrowest policy that lets it work — before
`0061` a single `s3:*` key was shared by the ETL, the API and every export, so the reader
could not be revoked without stopping the writer:

| role        | may touch                            | used by                                |
| ----------- | ------------------------------------ | -------------------------------------- |
| `base`      | `s3:*` on the bucket                 | provisioning / administrative use only |
| `datalake`  | rw `datalake/*`, `files/platforms/*` | the interlace pipeline, `app/lake`     |
| `documents` | rw `files/*`                         | the API's document + branding writes   |
| `readonly`  | ro `files/*`                         | exports, per-user read credentials     |

Access keys are **deterministic** (`svc-{bucket}` for base, `svc-{bucket}-{role}` otherwise),
which is what lets a repair ask the store about a specific principal. `ListBucket` is granted
with an `s3:prefix` condition because it is a bucket-level action: without the condition a
role scoped to `files/` could enumerate `datalake/`, which is provider data rather than the
firm's files. `base` is mirrored onto the legacy `storage.tenants.access_key_id` so pre-`0061`
readers keep working.

Properties:

- **Never blocks tenant creation.** Any failure logs a warning and leaves the row
  `provisioning`; `POST /admin/tenants/{id}/storage/provision` retries (idempotent,
  ensure-style throughout — re-running repairs). The same endpoint backfills tenants that
  predate this feature.
- **Repair verifies, it does not assume.** Each stored credential is probed against the store
  and re-minted when the store no longer authenticates it, distinguishing
  `InvalidAccessKeyId`/`SignatureDoesNotMatch` (principal gone → re-mint) from `AccessDenied`
  (principal alive, correctly unprivileged → leave alone). Reporting `service_user: ok`
  because the _registry_ held a key is what made a store that had lost its IAM users
  permanently unrepairable: every lake read 403'd while all four steps showed green.
- **Renaming the bucket prefix** (`S3_TENANT_BUCKET_PREFIX`) renames nothing by itself —
  `storage.tenants.bucket` is written once and treated as immutable. Use
  `apps/api/scripts/rename_tenant_buckets.py` (dry run by default): it copies the objects,
  rewrites the one `ducklake_metadata` row that names the bucket (DuckLake stores data-file
  paths _relative_ to `data_path`, so no snapshot is invalidated) and drops the service users
  so the next provision mints them against the new name.
- **Skipped** when S3 is unconfigured (blank endpoint/credentials — e.g. a fresh dev checkout;
  branding then uses the data-URI fallback) and for the model tenant (it holds no data).
- Disabling a tenant suspends the row; **buckets are never deleted automatically** —
  offboarding is a deliberate export (`exports/`) then a manual teardown.

### RustFS notes (local dev)

Bucket creation and bucket policies work over the plain S3 API (all `rustfs-init` uses),
and prefix-scoped anonymous policies are enforced (anonymous GET inside `files/tenant/` →
200, outside → 403). RustFS's IAM is immature — `mc admin user/policy` is incomplete and
its native admin API (`/rustfs/admin/v3/…`) still rejects our service-user mint — so
per-tenant service users are **best-effort behind an interface** (`app/object_store.py`):
`ensure_tenant_principal` returns the credential when the backend can mint one and `None`
otherwise (the registry records `access_key_id` NULL; a re-provision retries). The
production store choice decides which backend implements IAM for real.

## 8. Branding assets

`app/storage.py::put_branding_asset(..., proxy=True)` resolves storage in order:

1. the tenant's own bucket (`files/tenant/{kind}-{hash}.{ext}`) whenever its registry row is active;
2. the shared legacy `branding` bucket (`tenants/{tenant_id}/{kind}-{hash}.{ext}`) when not;
3. a base64 `data:` URI when no storage is configured at all.

Assets are content-hashed and served with an immutable one-year cache. Upload responses return
`src: "/_assets/{opaque-name}"`; they never return the S3 endpoint, bucket or key.

**Shell images are stored display-sized.** The logo, dark logo, icon and favicon
(`shell_images.SHELL_KINDS`) are painted at 36px tall in the sidebar header and on the login
splash, and a raster upload is downscaled before it is stored — long side at most 1024px
(`shell_images.MAX_SHELL_SIDE`), format preserved, SVG and ICO untouched — by
`app/shell_images.py::fit_shell_upload`, called from both upload handlers (`routers/firm.py`,
`routers/admin.py`) ahead of `put_branding_asset`. The long side is the wordmark's width, not the
36px height: a wide logo is ~180px of CSS across, and a 3× display of that is ~540 device pixels,
so the earlier 256px cap sat under the painted size and the browser enlarged it (soft in the
sidebar and on the splash). JPEG is stored at quality 95 with no chroma subsampling, which is
what keeps lettering from going soft. `Branding` has one `logo_url`, so the stored asset _is_ the
display-sized one; email mastheads and the illustration PDF read the same field. Card artwork and
content pictures are full-bleed and keep their size. Bytes that do not decode as the declared type
are a 422 at upload. A logo already stored at the old 256px cap stays that size until it is
uploaded again — the original is not kept.

Why: on staging the logo was a 3481×624 PNG held as a base64 `data:` URI (rung 3), and decoding
that bitmap on the main thread was the Largest Contentful Paint on 29 of 30 platform pages, two
seconds after the page had otherwise finished. A stored data URI cannot be turned into a cached URL
on read without writing an object, so the read side guards instead: `shell_images.shell_safe_src`
withholds an inline PNG/JPEG/WebP whose decoded bytes exceed 32 KiB or whose long side exceeds
1024px (`MAX_INLINE_SHELL_SIDE`, the same bound the upload stores, so a firm with no bucket still
sees the logo it just uploaded) — the portal payload (`environments.env_config_by_host`) and the admin shell read
(`/internal/admin-branding`) carry `null` and the shell falls back to the built-in mark. The
editor's own read (`branding.get_branding`) keeps the stored value, so a save from the studio does
not erase it; `scripts/restore_inline_branding.py` is still the way to move it onto the relay.
`pkgs/ui`'s `logoSources` applies the same byte rule a second time, so a cached payload cannot
reintroduce the bitmap, and the shell `<img>` carries `width`/`height` so its box is sized before
the bytes decode.

**Accepted logo types, and the email copy.** Both upload handlers go through
`app/branding_uploads.py::store`. SVG, PNG, JPEG, WebP and ICO are stored exactly as above. GIF,
AVIF, BMP, TIFF and HEIC/HEIF are also accepted — detected from the bytes, never the declared type
— and stored as a PNG of their first frame (a JPEG for an opaque photograph over the asset cap):
the portals cannot draw TIFF or HEIC, a BMP is far over the cap, and a HEIC's location metadata must
not reach this anonymous-read prefix. So the stored types, and the asset-name grammar, never grow
(`app/imaging.py`; HEIC needs `pi-heif`, a decode-only Pillow plugin).

A mail client draws PNG, JPEG and GIF inline and nothing else — Outlook shows an SVG or WebP logo as
a FILE ATTACHMENT above the message, and an SVG is broken in the body too. So a `logo` or `icon`
stored as SVG, WebP or ICO gets a PNG twin beside it, named from the original
(`logo-{digest}.svg` → `logo-email-{digest}.png`, `storage.email_copy_name`), fitted inside
640×256 with any transparent border trimmed. The original is never touched. The API converts
WebP and ICO with Pillow and SVG with CairoSVG (on the Cairo library the images already carry for
WeasyPrint); for an SVG the branding studio also draws the PNG in the browser and sends it with the
upload (`email_copy`, `pkgs/ui` `branding/email-copy.ts`), which is preferred when present. The send
path (`email/transport.py::email_assets`) uses the copy; with none — a logo uploaded before copies
existed — it converts the original in memory and stores the result, so the next send reads it.
Every answer is kept in memory per process (names are content-hashed, so the bytes never change).
With no picture at all (an SVG on a machine without Cairo) the masthead gives way to the icon, then
the firm's name in type. Copies are hidden from the email image library.

**Every public asset upload goes through this**, including the email editor's own image library —
which was the one hold-out, publishing the object-store URL instead (`proxy=True` on
`email_templates.py`'s upload). Two consequences of that: an author saw a broken picture in the
canvas wherever the browser could not reach the internal store, and the bytes only reached an inbox
because the send path inlines our own images as `cid:` attachments (`app/email/images.py`) — within
a four-image, 2 MiB budget, past which the URL travelled as authored and arrived broken.

**Email resolves the path to a URL at send.** `/_assets/{name}` means nothing in an inbox, so
`app/email/document.py::to_email_html` — the one seam every rendered email passes through — makes
every such path absolute against the firm's own portal host (`storage.absolute_asset_urls`, keyed
by `email/transport.py::asset_origin`). That covers both halves of an email's imagery:

- the **masthead**, resolved into `brand.logo_url` by `transport.brand_for`; with no reachable
  image the shell falls back to the firm's name set in type (`email/shell.py::masthead`), which is
  the placeholder a logo is supposed to replace;
- the **body**, where an author's own uploaded picture lives.

`asset_origin` takes no portal, deliberately: the relay resolves the tenant from whichever host it
was called on and then reads that tenant's bucket, so a firm's client and platform hosts serve the
same objects. Insisting on the sending portal's own host meant a firm with a client portal and no
platform host had its staff mail fall back to type while holding a servable logo.

With no host registered at all there is no URL to state, and the path is left as authored: a
relative `src` in an inbox is a broken image, and the firm's name in its own brand is better than
that.

**And then the bytes travel WITH the message.** An absolute URL is only as good as the recipient's
willingness to fetch it — most clients block a remote image until the reader asks — so
`send_email` resolves every relay asset the email names and attaches it by content id
(`src="cid:img1"`). The read is `storage.get_public_asset`, the same one the portal's relay
performs, in one short-lived `service_role` transaction per send; there is deliberately **no HTTP
request to the portal**, because the API may have no route to the public host (and none over https
on a dev box), and a request to a URL that came out of author-supplied markup is a question worth
not having to answer. The recipient needs no route to the firm at all.

Bounded at four images and 2 MiB, as the legacy object-store path already was, and past either
bound — or on any read failure — the image keeps its URL rather than being dropped. The absolute
URL is therefore the floor, not the mechanism.

## 9. Ingestion, SFTP and the fan-out

**SFTP exists only at base level.** One Graphene SFTP endpoint receives provider drops (SEI DSR
batches, Morgan Lloyd document packs); everything lands core-side under the provider's own
prefix (`platforms/sei/sftp/…`, `platforms/raw/{provider}/…` — §2).
Tenants never get SFTP — a tenant's view of provider data is the **grant-filtered fan-out**
into its own bucket:

```
providers (API / webhooks / SFTP)
  └─> platforms/raw/…                (write-once, whole-firm)
        └─> platforms/datalake/…       (typed parquet, catalogue lake_external)
              ├─> sink 1: OLTP upserts   (service_role — platform.transactions etc.)
              └─> sink 2: per-tenant fan-out
                    ├─> tnt-{uid}/datalake/{provider}/…    (rows matching the tenant's grants:
                    │                                    Seccl node lineage, SEI resolved DSR
                    │                                    grants, ML scheme/adviser scope)
                    └─> tnt-{uid}/files/provider/…     (associated documents/files)
```

Seccl client documents take this path. `raw.files` lands the PDF in the core bucket
(`raw/seccl/files/{firm}/{type}/{client}/`). `raw.file_copies` copies it into the tenant
bucket at `files/provider/seccl/{type}/{client}/{name}` and files a
`documents.documents` row (`source='provider'`, `deletable=false`) against the linked
client. `{name}` is Seccl's filename. That name is already the file id
(`0000FLMHS.pdf`, `BCEStatement-1721744514588.pdf`), so it is not prefixed again.
A generic name such as `note.pdf` still takes the file id in front, so two
documents that share it do not land on one key. There is no drop-date segment:
the document is immutable, and the day it was copied is not part of its identity.
The library's created date is the Seccl message `created` time. A file whose
client is not in the book yet waits for the next run. Seccl publishes
no firm document library — the PDFs are client messages.

Which rows fan out to which tenant is driven by **`platform.tenant_platforms`** —
a tenant enables a provider with the filter that selects its slice (Morgan Lloyd: the
connection's **Microsoft tenant id** — one Dynamics/Azure tenant per firm, stamped onto
every synced record as `ml_tenant_id`; Seccl: node ids; SEI: one `firm_id` plus optional
`subfirm_ids` and `dsr_ids` — see [`sei.md`](sei.md) §3, where an absent narrowing key
means "the whole firm" and the DSR column is table-dependent).
The admin pickers are fed **live** from the core warehouse via
`GET /admin/providers/{provider}/options` (SEI firms/subfirms/DSR teams from `ref.*`,
Seccl nodes from `raw_nodes`), since a firm's subfirms and its 400+ DSR teams change
whenever the custodian reorganises. `platform.providers` still names
hand-registered upstream ids, which is what Morgan Lloyd uses.
Enabling asks the provider's daemon to run that firm's chain immediately
(`platforms.trigger_tenant_refresh`; re-runnable per firm from the tenant page, or for
every firm via `POST /admin/platforms/{provider}/refresh`).

The operations surface for all of this is the admin **/platforms** section: per provider,
`app/platforms.py` aggregates the pipeline daemon (`interlace serve` — models/lineage/plan/
runs/events over its API, `INTERLACE_MORGAN_LLOYD_URL`), warehouse row counts and a
read-only SQL console (lake_reader, READ_ONLY attach on the core catalogue), plus the
sources + firm-enablement registries. Sources are registered there; enablement stays on
the firm's tenant page.

Tenant-facing bulk export = objects in their bucket (`exports/`), fetched with their scoped
credential or via the API — not a feed we push.

Morgan Lloyd and Seccl both run on the hosted warehouse (catalogue schemas `ml` / `seccl`
in the `datalakes` DB; parquet under `platforms/datalake/{provider}/` — the
`[bucket]/datalake/[provider]` convention holds core- and tenant-side). The per-firm
projection is **part of the provider's own DAG** now (`pkgs/book-sink`,
`docs/provider-pipeline.md`): each enabled firm gets `tenant_{hex}` → `stage_{hex}` →
`book_{hex}.merge_ledger` models inside the daemon, so a sync and the slices it feeds are
one run rather than two systems watching each other. The API's fan-out listener is gone
with `app/fanout.py` and `app/materialise.py`.
Morgan Lloyd still writes illustrations to `services/morgan-lloyd/.data/illustrations/`;
moving those follows the same shape.

## 10. The datalake — see `docs/datalake.md`

DuckLake catalogues live in the dedicated **`datalakes` Postgres database**
(`METADATA_SCHEMA` per tenant, named by the tenant uid, plus `core` for the staged zone);
parquet lives under each tenant bucket's `lake/` prefix; DuckDB is stateless ephemeral
compute. The API reads the lake **in-process** via `app/lake` — a pool of read-only,
single-tenant DuckDB attaches (a connection's scoped S3 secret and attach only reach one
tenant), returning Arrow tables / DataFrames and supporting lake↔OLTP joins. Full
reference: [`datalake.md`](datalake.md).

## 11. Open questions

- **WORM/object-lock on RustFS** — unverified; production regulated storage likely wants a
  store with proven Object Lock (or compliance mode) for `files/documents/` + `lake/archive/`.
- **Presigned URLs vs streaming** for authenticated document download — decide with the
  documents API.
- **Per-tenant CDN/custom domains** for public branding assets (`public_base_url` is already
  the seam).
