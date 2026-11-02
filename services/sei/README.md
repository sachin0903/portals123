# sei-sync

SEI Wealth Platform ingestion: nightly SFTP drop → `platforms` → PGP decrypt →
DuckLake warehouse (interlace v2). Full reference: [`docs/sei.md`](../../docs/sei.md).

## Layout

```
src/sei_sync/     the pull: settings, filename parsing, SFTP, decrypt, object store
models/           interlace models — raw_* (one per report) + ref.* (dimensions,
                  DSR types and the unpivoted DSR grant table)
data/emt_charges.csv
                  the EMT cost-and-charge extract, one current charge per ISIN
                  (scripts/build_emt_charges.py)
```

There is no `migrations/` directory: interlace models own their DDL and `apply` adds new
columns additively, so a new SEI field needs no schema change (docs/sei.md §2).

## Run

```bash
cp .env.example .env          # then fill in the SFTP + PGP + warehouse values
moon run sei-sync:install

moon run sei-sync:pull             # yesterday's drop from the live SFTP
moon run sei-sync:apply            # build the warehouse models
moon run sei-sync:dev              # the interlace daemon (API + scheduler) on :8103
```

`sei:pull` wraps `sei-sync`, which takes more options directly:

```bash
uv run sei-sync list --local-dir ~/dev/sei/p04-production
uv run sei-sync pull --since 2026-03-01 --until 2026-03-27
uv run sei-sync pull --report reference --report accounts
uv run sei-sync pull --local-dir ~/dev/sei/p04-production --limit 50
```

## Two things that will bite you

**SEI allowlists SFTP by source IP.** From a laptop, auth fails with `transport shut
down or saw EOF` even with correct credentials. Run the pull from an allowlisted host
(the Coolify host), or develop against a local directory of drop files with
`--local-dir`.

**`interlace apply` races when applying all 52 models at once** — it fails with
`Catalog write-write conflict on alter with "core_s3"`. Build by selector instead:

```bash
moon run sei-sync:apply -- --select raw_reference
moon run sei-sync:apply -- --select tag:ref
```

## Secrets

`.env` is gitignored and nothing in it belongs in the tree. The PGP private key is
supplied base64-encoded (`base64 -w0 graphene_gpg_privkey.asc` →
`SEI_PGP_PRIVATE_KEY_BASE64`); `SEI_PGP_PRIVATE_KEY_PATH` is the mounted-file fallback.
The tests generate their own throwaway key and never touch the real one.
