# Integrations

The Graphene-ops approval dashboard for which SEI custodian/software platforms a firm may use,
per-platform feature toggles, and job-run history. Ported from the legacy `platform` repo's
admin-only "Integrations" screen onto this stack, then reconciled (0273_integrations_tenant) once
`sei_reports` started writing to the same schema independently (0231_integrations_schema) and the
admin edit drawer's "Firm" field was repointed from an SEI firm code to a real portals tenant.

This is **not** `platform.providers`/`platform.tenant_platforms` — that pair is the closed,
three-provider (`morgan-lloyd`, `seccl`, `sei`) inbound data-sync pipeline (a tenant's connection
that pulls its book of business into the warehouse). This feature is a different concept that
happens to reuse the word "platform": an approval workflow, and Intelliflo isn't in that provider
list at all. `portals` used to have an `integrations` schema (migration `0029`) that was folded
into `platform` in `0048` — what got folded there was the provider-sync pair above, not this. The
name was free to reuse for a genuinely different thing.

## Model (`integrations` schema — migration `0231_integrations_schema`)

Shared with (and self-provisioned by) the `sei_reports` engine/webhook, which also writes to this
schema directly as the `app` login — see that migration's own header for why it lands defensively
(`IF NOT EXISTS` everywhere, exception-guarded role/grant setup).

- `integrations.firms`, `integrations.features`, `integrations.dsrs` — Graphene-curated reference
  data (which SEI firms, which platform features, which SEI DSR codes exist per firm). Seeded from
  the legacy system at migration time. `firms`/`dsrs` are no longer read by this screen (see
  "Retired" below) but stay as reference data `sei_reports` itself may still use.
- `integrations.platforms` — the approval row: one per (firm, platform, SEI tenant). `status`
  (`pending`/`approved`/`rejected`), `enabled`, `installed`, `application_data` (jsonb).
  `tenant_id`/`tenant_name` are **SEI's own** tenant identifiers for the custodian integration
  being approved — not a FK to `auth.tenants`. `firm_id` IS a real FK to `auth.tenants(id)`
  (0273_integrations_tenant) — the portals tenant this integration belongs to, picked from the
  edit drawer's Firm dropdown; `null` until an operator sets one. `actioned_by`/`justification`
  are set server-side only (never trusted from a request body); a `CHECK` requires a justification
  when rejecting.
- `integrations.platform_features` — which features are toggled on for a platform row.
- `integrations.job_runs` — history for a platform's feature syncs. Per-feature stats
  (`record_count`/`newly_added`/`failed`) live in a jsonb blob keyed by feature name, not their own
  columns — a port of the legacy shape, not a redesign.
- `integrations.schema_migrations`, `integrations.income_statement_items_sent`,
  `integrations.income_statements` — `sei_reports`' own bookkeeping and income-statement push
  state. Not read anywhere in this codebase.

**RLS**: every one of the 6 core tables (`firms`/`features`/`dsrs`/`platforms`/
`platform_features`/`job_runs`) is `ENABLE`+`FORCE ROW LEVEL SECURITY` with
**zero policies** and a grant to `service_role` only — the `aml.sync_state` pattern (proven by
`test_integrations_rls.py`). There is no tenant lineage predicate to write (this is Graphene-ops
data, not tenant data), so access is enforced entirely by `require_permission` in
`app/routers/integrations.py`, never by a WHERE-clause. Despite carrying columns literally named
`tenant_id`/`firm_id`, `integrations.platforms` is in `tenant_data.PRESERVED_TABLES` — it is
Graphene's own approval record, not tenant business data, and only `service_role` can reach it
regardless.

## Retired (0273_integrations_tenant)

The edit drawer's DSR picker and Provider field, and the Advisors tab, are gone. Both only made
sense paired with `firm_id` as an SEI firm code: DSR descriptions resolved via
`integrations.dsrs`, keyed by that same SEI code, and the Advisors tab's SEI/Intelliflo lookups
were scoped by it too. Once `firm_id` means a
portals tenant instead, none of that SEI-keyed reference data resolves against it any more. The
Provider field (`provider_id`/`provider_name`) was dropped in the same migration — logically
independent of `firm_id`, but the one dropdown that could ever set it went with the DSR picker,
and it had never been populated in production.

## Intelliflo Office (IO)

`pkgs/intelliflo` is the OAuth client (`INTELLIFLO_*` env vars — client-credentials grant against
Intelliflo's own identity host, not Azure AD). The token is cached in-process until near expiry.
Used by the platform portal's "Link to IO" cards and the admin Clients and Accounts tab (below) —
client/plan search and the link write-back, not an adviser-directory sync. No live Intelliflo
sandbox credentials exist for this port, so there is no `integration-test` task for the package —
`pkgs/intelliflo/tests` mock the HTTP layer only.

## API

`app/routers/integrations.py`, prefix `/integrations`, gated on `integrations:read`/
`integrations:write` (`app/permissions.py`, portal `operations` — `rbac.OPERATIONS_ONLY`, same
family as `lookthrough`/`platforms`). `GET /integrations/providers` from the legacy source was
**not** ported — it queried a table that never existed and nothing called it.

### Clients and Accounts

The same router also hosts the admin "Clients and Accounts" tab: a paginated, cross-tenant
`GET /integrations/clients-table` listing of every `crm.contacts` row (deliberately not scoped to
one tenant — see the router's own module note), each with its own Intelliflo client/account/plan
search and a `POST /integrations/clients-accounts-link` that writes both halves of the link in one
call via the same `crm._upsert_contact_io_link`/`_upsert_account_io_link` helpers the platform
portal's own "Link to IO" cards use, so the two paths cannot drift.

## UI

Admin `/integrations` (list + edit sheet) and `/integrations/{uid}` (detail: summary, Job Runs
tab, Clients and Accounts tab). Approve/reject requires a reason when rejecting, enforced in the
form, the router (422), and the DB (`CHECK`).

## Out of scope

A true "running" job-run state (nothing writes an in-flight row today, only a final
success/failure); a per-account (rather than per-client) Clients and Accounts row, so a client
with more than one account can only link one at a time through this tab.
