# Database schema (as implemented)

The current state of the Postgres schema, reflecting the applied SQL migrations
`apps/api/migrations/sql/0001`–`0004` plus the runtime permission registry
(`apps/api/app/permissions.py`). Migrations are SQL-first, forward-only (expand/contract).

- **DB roles (RLS principals):** `anon`, `authenticated` (the app role — DML-only, subject to
  RLS), `service_role` (`BYPASSRLS` — the API/sync/provisioning path). A separate elevated
  role runs migrations.
- **Schemas:** `auth`, `audit`, `platform`, `crm` (plus `public` for the shared
  `set_updated_at()` trigger fn).
- **Extensions:** `uuid-ossp`, `pgcrypto`.
- **Every tenant-scoped table** ships `ENABLE` + `FORCE ROW LEVEL SECURITY`, a read policy, a
  `SELECT`/write grant to `authenticated`, and `ALL` to `service_role`. Writes to sensitive
  tables are almost always `service_role`-only (no `authenticated` write policy ⇒ denied).
- **`updated_at`** is kept honest by a `BEFORE UPDATE` `public.set_updated_at()` trigger on
  every table carrying the column.
- **Audit:** sensitive tables carry an `audit_row` `AFTER INSERT/UPDATE/DELETE` trigger
  (`audit.record()`) writing to the append-only `audit.log`.

---

## Migration timeline

| Migration             | What it lands                                                                                                                                                                                                                                                                      |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0001_auth_core`      | `auth` + `audit` schemas; identity, tenants, WorkOS environment registry + encrypted secrets, API keys, the WorkOS RBAC mirror; claims/RLS helper fns; `auth.resolve_claims()` minter; audit spine + generic DML trigger.                                                          |
| `0002_platform_crm`   | `platform` + `crm` schemas; catalog (platforms/products/models/fee_packages); the CRM party model (contacts + bridges/book/notes/relationships/applications); accounts + transactions; two-layer client-scoped RLS; `resolve_claims` extended to fill `contact_ids`/`account_ids`. |
| `0003_platform_links` | `platform.links` — polymorphic canonical-entity → provider-external-id bridge, scoped like the entity it points at.                                                                                                                                                                |
| `0004_portal_scope`   | Makes the **client portal** a hard trust boundary: `auth.portal()` claim; `tenant_lineage()` empty and `can_read_all()` false in the client portal; `resolve_claims` classifies the active org → portal.                                                                           |

---

## Schema `auth`

Identity, tenancy, and the WorkOS RBAC mirror. WorkOS is the system of record for the mirror
tables; the sync worker (`service_role`) writes them, the app role only reads them. Runtime
authz reads JWT claims, never these tables.

### `auth.users` — principal identity (one row per login)

Project-per-tenant: a login belongs to exactly one tenant; email is unique **within** a tenant,
never globally.

| Column                                            | Type        | Notes                                                                   |
| ------------------------------------------------- | ----------- | ----------------------------------------------------------------------- |
| `id`                                              | uuid PK     | `uuid_generate_v4()`                                                    |
| `uid`                                             | text        | NOT NULL, UNIQUE                                                        |
| `tenant_id`                                       | int         | FK → `auth.tenants(id)` ON DELETE CASCADE; nullable during expand phase |
| `email`                                           | text        | NOT NULL                                                                |
| `first_name`, `last_name`, `mobile`               | text        |                                                                         |
| `auth_id`                                         | text        |                                                                         |
| `workos_user_id`                                  | text        | UNIQUE                                                                  |
| `invitation_id`                                   | text        |                                                                         |
| `invitation_sent_at`/`_expires_at`/`_accepted_at` | timestamptz |                                                                         |
| `last_sign_in`                                    | timestamptz |                                                                         |
| `avatar`                                          | text        |                                                                         |
| `enabled`                                         | boolean     | NOT NULL DEFAULT false (invited users disabled until acceptance)        |
| `created_at`/`updated_at`                         | timestamptz | NOT NULL DEFAULT now()                                                  |
| `deleted_at`                                      | timestamptz |                                                                         |
| `name`                                            | text        | GENERATED STORED (trimmed `first_name` ‖ `last_name`)                   |

Constraints: `UNIQUE (tenant_id, email)`.
Indexes: `enabled`; `workos_user_id` (partial); `tenant_id` (partial).
**RLS:** SELECT — own row OR a member of a tenant in the caller's lineage. Writes `service_role` only.

### `auth.tenants` — the tenant/firm entity

Self-referential hierarchy with a materialised `lineage[]`. This table **is** the tenant, so it
is not tenant-scoped.

| Column                      | Type    | Notes                                               |
| --------------------------- | ------- | --------------------------------------------------- |
| `id`                        | int PK  | GENERATED BY DEFAULT AS IDENTITY                    |
| `uid`                       | text    | NOT NULL, UNIQUE                                    |
| `parent_id`                 | int     | FK → `auth.tenants(id)` ON DELETE SET NULL          |
| `alias`                     | text    | NOT NULL, UNIQUE                                    |
| `name`                      | text    | NOT NULL                                            |
| `lineage`                   | int[]   | maintained by triggers: `[self, …ancestors]`        |
| `workos_project_id`         | text    | UNIQUE (project-per-tenant)                         |
| `workos_organisation_id`    | text    | UNIQUE (transitional default/portal org)            |
| `website`, `email`, `phone` | text    |                                                     |
| `domains`                   | jsonb   |                                                     |
| `icon`, `logo`              | text[]  |                                                     |
| `theme`                     | jsonb   | white-label branding (structured, validated)        |
| `frn`                       | int     |                                                     |
| `chn`                       | text    |                                                     |
| `localisation`              | jsonb   |                                                     |
| `base_currency`             | text    | FK → `ref.currencies(code)`, default `GBP`          |
| `allowed_currencies`        | text[]  | non-empty ISO allow-list; contains `base_currency`  |
| `enabled`                   | boolean | DEFAULT true                                        |
| `is_model`                  | boolean | NOT NULL DEFAULT false (the single template tenant) |

Constraints: `CHECK (parent_id IS NULL OR parent_id <> id)`; the currency allow-list is non-empty,
contains no nulls, and includes the base currency. A trigger also refuses allow-list members absent
from `ref.currencies` (an array cannot carry a foreign key per member).
Indexes: `enabled`; `domains` (gin, partial); `parent_id`, `workos_organisation_id`,
`workos_project_id` (partial).
Triggers: `tenants_lineage_trigger` (BEFORE INSERT/UPDATE — build lineage, reject cycles);
`tenants_lineage_descendants_trigger` (AFTER UPDATE OF `parent_id` — recompute descendants).
Seed: one `is_model = true` "Model Tenant" (`ten_model`/`model`).
**RLS:** SELECT `USING (true)` (directory readable; scoping it would recurse). Writes `service_role` only.

### `auth.tenant_environments` — per-tenant WorkOS environment registry

Non-secret routing/config. One row per (tenant, stage); one deployment holds one stage.

| Column                                    | Type        | Notes                                             |
| ----------------------------------------- | ----------- | ------------------------------------------------- |
| `id`                                      | int PK      | IDENTITY                                          |
| `tenant_id`                               | int         | NOT NULL, FK → `auth.tenants` ON DELETE CASCADE   |
| `stage`                                   | text        | NOT NULL, CHECK in (`dev`,`staging`,`production`) |
| `workos_client_id`                        | text        | selects the JWKS for token verification           |
| `issuer`                                  | text        | token `iss` → tenant lookup                       |
| `client_portal_host`, `platform_host`     | text        | host → tenant/environment resolution              |
| `client_portal_org_id`, `platform_org_id` | text        | the two portal WorkOS orgs                        |
| `operations_org_id`                       | text        | Graphene ops org (Graphene tenant only)           |
| `events_cursor`                           | text        | last WorkOS Events API cursor                     |
| `created_at`/`updated_at`                 | timestamptz |                                                   |

Constraints: `UNIQUE (tenant_id, stage)`.
Indexes: `tenant_id`; `issuer`, `workos_client_id`, `client_portal_host`, `platform_host` (partial).
**RLS:** SELECT within caller's lineage. Writes `service_role` only.

### `auth.environment_secrets` — Fernet-encrypted per-environment secrets

`service_role`-**only** (no `authenticated` grant/policy — even ciphertext is unreadable on the request path).

| Column                    | Type        | Notes                                                               |
| ------------------------- | ----------- | ------------------------------------------------------------------- |
| `id`                      | int PK      | IDENTITY                                                            |
| `tenant_environment_id`   | int         | NOT NULL, UNIQUE, FK → `auth.tenant_environments` ON DELETE CASCADE |
| `workos_api_key_ct`       | text        | Fernet ciphertext of WorkOS API key                                 |
| `cookie_password_ct`      | text        | Fernet ciphertext of session cookie password                        |
| `created_at`/`updated_at` | timestamptz |                                                                     |

### `auth.api_keys` — Graphene-issued credentials

Tenant-scoped; optionally user-linked. Only a SHA-256 hash + prefix + scopes stored.

| Column                                     | Type        | Notes                                                            |
| ------------------------------------------ | ----------- | ---------------------------------------------------------------- |
| `id`                                       | int PK      | IDENTITY                                                         |
| `uid`                                      | text        | NOT NULL, UNIQUE (public-facing id)                              |
| `tenant_id`                                | int         | NOT NULL, FK → `auth.tenants` ON DELETE CASCADE                  |
| `user_id`                                  | uuid        | FK → `auth.users` ON DELETE SET NULL (NULL ⇒ tenant service key) |
| `name`                                     | text        | NOT NULL                                                         |
| `key_prefix`                               | text        | NOT NULL                                                         |
| `key_hash`                                 | text        | NOT NULL, UNIQUE (SHA-256 of secret)                             |
| `scopes`                                   | text[]      | NOT NULL DEFAULT `{}`                                            |
| `last_used_at`, `expires_at`, `revoked_at` | timestamptz |                                                                  |
| `revoked_by`                               | uuid        | FK → `auth.users` ON DELETE SET NULL                             |
| `created_at`                               | timestamptz | NOT NULL DEFAULT now()                                           |
| `created_by`                               | uuid        | FK → `auth.users` ON DELETE SET NULL                             |

Indexes: `tenant_id`; `user_id` (partial); `key_prefix`.
**RLS:** read + write within caller's lineage (`authenticated` may manage its own firm's keys).

### WorkOS RBAC mirror

**`auth.roles`** — tenant-scoped, configurable (no global templates).

| Column                    | Type        | Notes                                                                    |
| ------------------------- | ----------- | ------------------------------------------------------------------------ |
| `id`                      | int PK      | IDENTITY                                                                 |
| `workos_role_id`          | text        | UNIQUE (NULL for local/pre-sync)                                         |
| `tenant_id`               | int         | FK → `auth.tenants` ON DELETE CASCADE (NULL ⇒ template)                  |
| `slug`                    | text        | NOT NULL                                                                 |
| `name`                    | text        | NOT NULL                                                                 |
| `type`                    | text        | NOT NULL DEFAULT `organisation`, CHECK in (`environment`,`organisation`) |
| `portal`                  | text        | CHECK in (`client`,`platform`,`operations`)                              |
| `description`             | text        |                                                                          |
| `created_at`/`updated_at` | timestamptz |                                                                          |

Constraints: `UNIQUE NULLS NOT DISTINCT (tenant_id, slug)`. Index: `tenant_id`.
**RLS:** SELECT where `tenant_id IS NULL OR tenant_id = ANY(lineage)`. Writes `service_role` only.

**`auth.permissions`** — the RBAC catalogue (upserted from `app/permissions.py` on startup).

| Column                    | Type        | Notes                                       |
| ------------------------- | ----------- | ------------------------------------------- |
| `id`                      | int PK      | IDENTITY                                    |
| `workos_permission_id`    | text        | UNIQUE                                      |
| `slug`                    | text        | NOT NULL, UNIQUE (`resource:action`)        |
| `name`                    | text        | NOT NULL                                    |
| `resource`, `action`      | text        |                                             |
| `portal`                  | text        | CHECK in (`client`,`platform`,`operations`) |
| `description`             | text        |                                             |
| `created_at`/`updated_at` | timestamptz |                                             |

**RLS:** SELECT `USING (true)`. Writes `service_role` only.

**`auth.role_permissions`** — join.

| Column          | Type | Notes                                               |
| --------------- | ---- | --------------------------------------------------- |
| `role_id`       | int  | NOT NULL, FK → `auth.roles` ON DELETE CASCADE       |
| `permission_id` | int  | NOT NULL, FK → `auth.permissions` ON DELETE CASCADE |
| PK              |      | `(role_id, permission_id)`                          |

Index: `permission_id`. **RLS:** SELECT `USING (true)`; writes `service_role` only.

**`auth.organisation_memberships`** — one row per (user, tenant, portal).

| Column                    | Type        | Notes                                                                    |
| ------------------------- | ----------- | ------------------------------------------------------------------------ |
| `id`                      | int PK      | IDENTITY                                                                 |
| `workos_membership_id`    | text        | UNIQUE                                                                   |
| `user_id`                 | uuid        | NOT NULL, FK → `auth.users` ON DELETE CASCADE                            |
| `tenant_id`               | int         | NOT NULL, FK → `auth.tenants` ON DELETE CASCADE                          |
| `role_id`                 | int         | FK → `auth.roles` ON DELETE SET NULL                                     |
| `portal`                  | text        | NOT NULL DEFAULT `platform`, CHECK in (`client`,`platform`,`operations`) |
| `status`                  | text        | NOT NULL DEFAULT `active`, CHECK in (`active`,`inactive`,`pending`)      |
| `created_at`/`updated_at` | timestamptz |                                                                          |

Constraints: `UNIQUE (user_id, tenant_id, portal)`.
Indexes: `user_id`, `tenant_id`, `role_id` (partial).
**RLS:** SELECT where `tenant_id = ANY(lineage) OR user_id = auth.uid()`. Writes `service_role` only.

### `auth` helper functions (claims / RLS)

All `STABLE`; the request layer mints pre-resolved claims so policy evaluation does zero `auth.*` queries per row.

- `auth.jwt()` → the `request.jwt.claims` jsonb.
- `auth.uid()` → uuid (`user_uuid` claim); `auth.role()` → text; `auth.workos_org()` → text; `auth.session_id()` → text.
- `auth.tenant_id()` → int (pre-resolved, `workos_org` fallback).
- `auth.tenant_lineage()` → int[] — caller's tenant + descendants; **empty** for a client-portal session or `role='client'` (0004).
- `auth.jwt_permissions()` → text[]; `auth.is_system()` → bool; `auth.has_role()` / `auth.has_permission()` → bool.
- `auth.can_read_all()` → bool — `contacts:read_all`, but **false** in the client portal (0004).
- `auth.contact_scope()` / `auth.account_scope()` → int[] — fail-closed (absent claim ⇒ empty).
- `auth.portal()` → text (`client`|`platform`|`operations`; 0004).
- **`auth.resolve_claims(workos_user_id, workos_org_id)`** → jsonb — SECURITY DEFINER (owned by `service_role`, BYPASSRLS). The per-request minter: resolves user/tenant/lineage, classifies the portal, and mints `contact_ids`/`account_ids` (own linked parties via `crm.contact_users`, plus adviser book via `crm.client_advisers` outside the client portal; then their accounts). Called once per request by `app/db.py::tenant_session`.
- `auth.lineage()` / `auth.lineage_descendants()` — the tenant-hierarchy triggers (SECURITY DEFINER, `service_role`-owned).

### Runtime permission registry (`app/permissions.py` → `auth.permissions`)

Taxonomy: each resource gets `read` + `write` (**write implies read**); `system` bypasses everything.

| Slug                                                                                  | Portal     | Purpose                                             |
| ------------------------------------------------------------------------------------- | ---------- | --------------------------------------------------- |
| `members:read` / `members:write`                                                      | platform   | Firm's people + portal access                       |
| `roles:read` / `roles:write`                                                          | platform   | Firm's roles + permissions                          |
| `branding:read` / `branding:write`                                                    | platform   | White-label branding                                |
| `contacts:read` / `contacts:write` / `contacts:create` / `contacts:sync_morgan_lloyd` | platform   | Clients — view / edit / mint / push to Morgan Lloyd |
| `contacts:read_all`                                                                   | platform   | RLS "see all vs own book" lever                     |
| `tenants:read` / `tenants:write`                                                      | operations | Firms + members/roles/environments                  |
| `operations:read` / `operations:write`                                                | operations | Graphene internal-ops staff/roles                   |
| `model:read` / `model:write`                                                          | operations | The model tenant                                    |
| `audit:read`                                                                          | operations | Append-only audit trail                             |
| `system`                                                                              | operations | Cross-tenant superuser (`auth.is_system()`)         |

---

## Schema `crm` — the party domain

`crm.contacts` **is** the client (Decision 8): there is no separate client entity. Enums:
`contact_type` (individual/organisation), `contact_sub_type` (trust/company/charity),
`account_role` (owner/decision_maker/trustee/beneficiary/attorney/other), `relationship_type` (spouse/…/other),
`service_type` (advised/execution_only).

### `crm.contacts`

| Column                                                  | Type                 | Notes                                                                                                                                  |
| ------------------------------------------------------- | -------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                                                    | int PK               | IDENTITY                                                                                                                               |
| `tenant_id`                                             | int                  | NOT NULL, FK → `auth.tenants` ON DELETE CASCADE                                                                                        |
| `uid`                                                   | text                 | NOT NULL, UNIQUE                                                                                                                       |
| `type`                                                  | crm.contact_type     | NOT NULL                                                                                                                               |
| `enabled`                                               | boolean              | NOT NULL DEFAULT true                                                                                                                  |
| `service_type`                                          | crm.service_type     | advised / execution_only                                                                                                               |
| `currency`                                              | text                 | NOT NULL DEFAULT `GBP`, FK → `ref.currencies(code)`. The client's base currency.                                                       |
| `sub_type`                                              | crm.contact_sub_type |                                                                                                                                        |
| `name`, `title`, `first_name`, `middle_name`, `surname` | text                 |                                                                                                                                        |
| `dob`                                                   | date                 |                                                                                                                                        |
| `nationality`, `tax_residency`                          | text[]               |                                                                                                                                        |
| `national_identifier`, `national_identifier_type`       | text                 |                                                                                                                                        |
| `email`                                                 | text                 |                                                                                                                                        |
| `mobile`                                                | text                 | the mobile — the one number every custodian can store                                                                                  |
| `phone`                                                 | text                 | the other number. Morgan Lloyd `telephone1`, a second SEI contact-details number, Seccl's `phone` object. Fax and pager are not stored |
| `address`, `bank_account`, `data`                       | jsonb                |                                                                                                                                        |
| `created_at`/`updated_at`                               | timestamptz          |                                                                                                                                        |
| `deleted_at`                                            | timestamptz          |                                                                                                                                        |

Constraints: `CHECK (type='organisation' OR sub_type IS NULL)`.
Indexes: `tenant_id`, `uid`, `(tenant_id,type)`, `(tenant_id,enabled)`.
**RLS:** `(can_read_all AND tenant_id = ANY(lineage)) OR id = ANY(contact_scope())`.

### `crm.contact_users` — login ↔ party bridge (M:N)

`id` PK; `tenant_id`, `contact_id` (→ contacts CASCADE), `user_id` (→ auth.users CASCADE);
`capacity` text NOT NULL DEFAULT `self`, CHECK in (`self`,`authorised`,`power_of_attorney`,`delegate`);
`created_at`. `UNIQUE (contact_id, user_id)`. Indexes on tenant/contact/user.
**RLS:** `user_id = auth.uid() OR (can_read_all AND lineage) OR contact_id = ANY(contact_scope())`.

### `crm.client_advisers` — a client's team; one adviser (0245)

`id` PK; `tenant_id`, `contact_id` (CASCADE), `adviser_id` (→ auth.users CASCADE); `role` text NOT
NULL, `CHECK (role IN ('adviser','paraplanner','administrator','support'))`
(`client_advisers_role_known`); `created_at`. `UNIQUE (contact_id, adviser_id)`, and
`client_advisers_one_adviser_key UNIQUE (contact_id) WHERE role = 'adviser'` — one adviser per
client, any number of other staff. Indexes on tenant/contact/adviser.
**RLS:** `adviser_id = auth.uid() OR (can_read_all AND lineage) OR contact_id = ANY(contact_scope())`.

### `crm.contact_notes` — staff-internal notes

`id` PK; `tenant_id`, `contact_id` (CASCADE), `author_id` (→ auth.users SET NULL); `body` NOT NULL;
`created_at`. Indexes `(contact_id, created_at DESC)`, `tenant_id`.
**RLS:** `tenant_id = ANY(lineage) AND (can_read_all OR contact_id = ANY(contact_scope()))` — a client never reads notes about themselves.

### `crm.relationships` — party graph

`id` PK; `tenant_id`; `from_contact_id`, `to_contact_id` (both → contacts CASCADE);
`relationship_type` NOT NULL; `data` jsonb; `created_at`.
`UNIQUE (from_contact_id, to_contact_id, relationship_type)`. Indexes on tenant/from/to.
**RLS:** `tenant_id = ANY(lineage) AND (can_read_all OR from/to ∈ contact_scope())`.

### `crm.applications` — onboarding state

| Column                                      | Type                  | Notes                                                   |
| ------------------------------------------- | --------------------- | ------------------------------------------------------- |
| `id`                                        | int PK                | IDENTITY                                                |
| `tenant_id`                                 | int                   | NOT NULL, FK → auth.tenants CASCADE                     |
| `platform_id`                               | int                   | NOT NULL, FK → platform.platforms CASCADE               |
| `adviser_id`                                | uuid                  | FK → auth.users SET NULL                                |
| `uid`                                       | text                  | NOT NULL, UNIQUE                                        |
| `status`                                    | text                  |                                                         |
| `custody`                                   | platform.custody_type |                                                         |
| `adviser_declaration`                       | boolean               | DEFAULT false                                           |
| `adviser_declaration_on`                    | timestamptz           |                                                         |
| `adviser_declaration_by`                    | uuid                  | FK → auth.users SET NULL                                |
| `synced`, `synced_on`, `sync_error_message` |                       |                                                         |
| `offshore_approved`/`_on`/`_by`             |                       | `_by` FK → auth.users SET NULL                          |
| `created_at`/`updated_at`/`deleted_at`      | timestamptz           |                                                         |
| `enabled`                                   | boolean               | GENERATED (`deleted_at IS NULL`)                        |
| `completed`                                 | boolean               | GENERATED (`status='completed' AND deleted_at IS NULL`) |

Indexes: `enabled`, `adviser_id` (partial), `tenant_id`, `status` (partial), `uid`.
**RLS:** `(can_read_all AND lineage) OR adviser_id = auth.uid()`.

### `crm.account_contacts` — application/account ↔ contact join

`id` PK; `tenant_id`; `application_id` (NOT NULL → applications CASCADE); `account_id`
(→ platform.accounts SET NULL, added after accounts); `contact_id` (→ contacts SET NULL);
`role` crm.account_role. `UNIQUE NULLS NOT DISTINCT (application_id, account_id, contact_id, role)`.
Indexes on tenant/application/account/contact.
**RLS:** `(can_read_all AND lineage) OR contact_id ∈ contact_scope() OR account_id ∈ account_scope()`.

---

## Schema `platform` — catalog, accounts, transactions, provider links

Enums: `product_type` (GIA/ISA/JISA/SIPP/TPONBOND/TPOFFBOND/TPPENSION), `custody_type`
(onshore/offshore), `fee_frequency` (initial/ongoing), `fee_type` (fixed/percentage/tiered),
`fee_access` (hidden/display/select/custom), `account_status` (draft/active/closed),
`transaction_type` (buy/sell/dividend/interest/fee/tax/transfer_in/transfer_out/deposit/withdrawal/corporate_action/adjustment).

### Reference catalog

**`platform.provider_code`** — the ENUM (`sei | seccl | morgan-lloyd`), mirroring `app/providers.py::PROVIDERS`. It replaced the `platform.platforms` TABLE in 0131: that table held one row per provider and existed only to be a foreign-key target, so eight columns joined to it to learn a value they now carry. Members are added and never removed — `platform.merge_runs` rows must keep reading as words after we stop integrating with someone.
**`platform.products`** — `id` PK; `uid`; `tenant_id` (CASCADE, nullable — a NULL row is a legacy shared catalogue entry the policy exposes read-only); `product_type` NOT NULL; `name` NOT NULL; `code`; `description`; `currency`; `is_pension`; `bank_details_allowed` NOT NULL DEFAULT true (0167; JISAs backfilled false — whether accounts of this product may hold withdrawal destination bank details); `sort_order`; `enabled` DEFAULT true. RLS SELECT `tenant_id IS NULL OR tenant_id = ANY (auth.tenant_lineage()) OR tenant_id = auth.tenant_id()` + FORCE (0042 lineage-only, which hid every firm-scoped row from the client portal; **0177** added `auth.tenant_id()` so a client can read their own firm's shelf — `/crm/home` labels wrapper cards from this join). **A product names its platforms through `platform.product_platforms`, not a column**: 0044 moved the reference to the child so one shelf item can name a custodian AND a scheme administrator (0122's `role`), and 0130 dropped `products.platform_id`/`platform_identifier`/`data`, none of which had a reader.
**`platform.product_platforms`** — `id` PK; `tenant_id`; `product_id`; `provider` (`platform.provider_code`, 0131 — was a `platform_id` FK); `external_id` (the value the vendor's API takes); `external_name`; `role` (custodian | scheme_administrator | wrapper_provider, 0122, stamped by the pipeline from the provider's declared role); `data` (allowlisted — `ml_scheme_type`/`ml_sipp_type`); `enabled`. `UNIQUE (product_id, provider)`; several providers may hold the same role for one shelf item (for example a SIPP offered through both Seccl and SEI). Enabling a scheme administrator (`providers.set_provider`) attaches every **SIPP** on the firm's shelf to that provider as `role='scheme_administrator'`; a SIPP created afterwards gets the same mapping. A third-party pension (`TPPENSION`) is not attached — `is_pension` is the display flag, not this one. Disabling does not delete a SIPP row. Enabling SEI attaches **GIA, ISA, JISA and SIPP** as `role='custodian'` (`providers.attach_custodians`). A null role is filled with the provider's declared role and is not replaced once set; `external_id`, `account_open_reference` and `enabled` are left as the firm set them. A third-party bond or pension is not attached. An account that still has no custodian takes SEI when that product names no other enabled custodian. `0259_sei_product_custodian` is the one-shot for a firm already enabled.
**`platform.models`** — `id` PK; `platform_id` (NOT NULL CASCADE); `tenant_id` (NOT NULL CASCADE); `name` NOT NULL; `platform_identifier`; `products` product_type[]; `enabled`. RLS SELECT within lineage.
**`platform.fee_packages`** — `id` PK; `tenant_id` (NOT NULL CASCADE); `platform_id`/`product_id`/`model_id` (SET NULL); `external_id`; `name`/`category`/`frequency`/`fee_type` NOT NULL; `rule` jsonb NOT NULL; `access` DEFAULT `hidden`; `description`; `enabled`; `is_default`; `optional`; `sort_order`. RLS SELECT within lineage.

All catalog: writes `service_role` only.

### `platform.accounts` — owned by a contact (never a login)

| Column                                                               | Type                    | Notes                                                                    |
| -------------------------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------ |
| `id`                                                                 | int PK                  | IDENTITY                                                                 |
| `tenant_id`                                                          | int                     | NOT NULL, FK → auth.tenants CASCADE                                      |
| `contact_id`                                                         | int                     | FK → crm.contacts **ON DELETE RESTRICT**                                 |
| `application_id`                                                     | int                     | FK → crm.applications SET NULL                                           |
| `product_id`, `model_id`                                             | int                     | FK, SET NULL                                                             |
| `fee_package_id`                                                     | int                     | Deprecated compatibility FK; new reads/writes use `account_fee_packages` |
| `primary_custodian`                                                  | int                     | FK → platform.platforms SET NULL                                         |
| `status`                                                             | platform.account_status | NOT NULL DEFAULT `draft`                                                 |
| `uid`                                                                | text                    | UNIQUE                                                                   |
| `name`, `risk_level`                                                 | text                    |                                                                          |
| `custody`                                                            | platform.custody_type   |                                                                          |
| `currency`, `investment_capacity`, `dividend_treatment`              | text                    |                                                                          |
| `fees`, `payments`, `withdrawals`, `transfer`, `data`, `performance` | jsonb                   |                                                                          |
| `platform_identifier`                                                | text                    |                                                                          |
| `value`                                                              | numeric(20,4)           |                                                                          |
| `opened_at`, `closed_at`, `created_at`, `updated_at`                 | timestamptz             |                                                                          |

Indexes: tenant; contact/application/product/model/fee_package (partial); status.
**RLS:** `(can_read_all AND lineage) OR id ∈ account_scope() OR contact_id ∈ contact_scope()`.

**`platform.account_fee_packages`** — tenant-safe account ↔ fee-package junction. Unique on
`(account_id, fee_package_id)` with `sort_order` for display only; all selected packages are equal,
so there is no primary flag or mirror back to `accounts.fee_package_id`. RLS follows account scope
or tenant-lineage `read_all`; writes run through the tenant-locked service session.

### `platform.transactions` — the realised fact (immutable, idempotent upsert)

| Column                                                | Type                      | Notes                                                                  |
| ----------------------------------------------------- | ------------------------- | ---------------------------------------------------------------------- |
| `id`                                                  | bigint PK                 | IDENTITY                                                               |
| `tenant_id`                                           | int                       | NOT NULL, FK → auth.tenants CASCADE                                    |
| `account_id`                                          | int                       | FK → platform.accounts SET NULL                                        |
| `contact_id`                                          | int                       | FK → crm.contacts SET NULL                                             |
| `expectation_id`                                      | bigint                    | FK → integrations.transaction_expectations (wired in a later revision) |
| `source_system`, `source_transaction_id`              | text                      | NOT NULL                                                               |
| `type`                                                | platform.transaction_type | NOT NULL                                                               |
| `source_type`, `source_subtype`, `isin`, `asset_name` | text                      |                                                                        |
| `amount`                                              | numeric(20,4)             | NOT NULL                                                               |
| `currency`                                            | text                      | NOT NULL DEFAULT `GBP`                                                 |
| `quantity`                                            | numeric(28,8)             |                                                                        |
| `price`                                               | numeric(20,8)             |                                                                        |
| `settlement_amount`                                   | numeric(20,4)             |                                                                        |
| `settlement_currency`                                 | text                      |                                                                        |
| `fee_amount`, `tax_amount`                            | numeric(20,4)             |                                                                        |
| `transaction_date`, `value_date`, `settlement_date`   | date                      |                                                                        |
| `posted_at`                                           | timestamptz               |                                                                        |
| `cgt`                                                 | boolean                   |                                                                        |
| `report_group`, `status`                              | text                      |                                                                        |
| `data`                                                | jsonb                     |                                                                        |
| `synced_at`, `created_at`, `updated_at`               | timestamptz               |                                                                        |

Constraints: `UNIQUE (tenant_id, source_system, source_transaction_id)`. Two firms syncing one custodian firm each keep a copy.
Indexes: account; contact (partial); `(tenant_id, transaction_date)`; type; income partial (`type IN (dividend,interest)`).
**RLS:** `(can_read_all AND lineage) OR account_id ∈ account_scope() OR contact_id ∈ contact_scope()`.

### `platform.sei_money_holds` — one SEI money-out in flight (0260)

| Column       | Type        | Notes                                     |
| ------------ | ----------- | ----------------------------------------- |
| `tenant_id`  | int         | PK, FK → auth.tenants CASCADE             |
| `account_id` | int         | PK, FK → accounts (tenant_id, id) CASCADE |
| `kind`       | text        | PK (`withdrawal`, `adhoc_fee`, …)         |
| `origin_uid` | text        | The instruction the hold was taken for    |
| `created_at` | timestamptz |                                           |

Committed on the service connection before the SEI call. The pending booking
stays in the request transaction, which still holds that row's unique key, so
the hold cannot be a second copy of it. A rolled-back request leaves the hold
in place and a new instruction uid for the same account and kind is refused.
SEI's definite refusal deletes it. A held instruction that is then `submitted`
or `posted` releases the account for the next payment.

**RLS:** forced, with no policy for `authenticated`. Written by `service_role`.

### `platform.links` — canonical entity → provider external id (0003)

| Column                    | Type        | Notes                                    |
| ------------------------- | ----------- | ---------------------------------------- |
| `id`                      | bigint PK   | IDENTITY                                 |
| `tenant_id`               | int         | NOT NULL, FK → auth.tenants CASCADE      |
| `entity_type`             | text        | NOT NULL (`contact`/`account`/`model`/…) |
| `entity_id`               | bigint      | NOT NULL (canonical OLTP id)             |
| `provider`                | text        | NOT NULL (`seccl`/`sei`/`ml`/…)          |
| `link_type`               | text        | NOT NULL DEFAULT `primary`               |
| `external_id`             | text        | NOT NULL                                 |
| `data`                    | jsonb       |                                          |
| `created_at`/`updated_at` | timestamptz |                                          |

Constraints: `UNIQUE (provider, entity_type, entity_id, link_type)`.
Indexes: `(entity_type, entity_id)`; `(provider, external_id)`; tenant.
**RLS:** `(can_read_all AND lineage) OR (entity_type='account' AND entity_id ∈ account_scope()) OR (entity_type='contact' AND entity_id ∈ contact_scope())`.

---

## Schema `audit`

### `audit.log` — append-only trail (FCA evidence)

| Column        | Type        | Notes                                             |
| ------------- | ----------- | ------------------------------------------------- |
| `id`          | bigint PK   | IDENTITY                                          |
| `tenant_id`   | int         | FK → auth.tenants CASCADE                         |
| `user_id`     | uuid        | FK → auth.users SET NULL                          |
| `action`      | text        | NOT NULL (`schema.table.op`)                      |
| `entity_type` | text        | `schema.table`                                    |
| `entity_id`   | bigint      | (integer PKs only; others live in `detail`)       |
| `detail`      | jsonb       | changed columns / row snapshot (secrets redacted) |
| `created_at`  | timestamptz | NOT NULL DEFAULT now()                            |

Indexes: `(entity_type, entity_id)`; tenant/user (partial); `created_at`.
**RLS:** SELECT tenant-scoped (`lineage`, or `tenant_id IS NULL AND is_system()`); INSERT `WITH CHECK (true)` (append can never be blocked). Grant is `SELECT, INSERT` only to `authenticated` — no UPDATE/DELETE (append-only).

- **`audit.record()`** — the generic DML trigger (SECURITY DEFINER, `service_role`-owned). Captures every write, stamps the affected row's tenant + `auth.uid()`, redacts `key_hash`/`credentials_ref`/`workos_api_key_ct`/`cookie_password_ct`; UPDATE logs only changed columns.
- **`audit.attach(text[])`** — idempotently attaches the `audit_row` trigger.

**Audited tables:** `auth.users`, `auth.tenants`, `auth.tenant_environments`, `auth.environment_secrets`, `auth.api_keys`, `auth.roles`, `auth.organisation_memberships`; `crm.contacts`, `crm.contact_users`, `crm.client_advisers`, `crm.account_contacts`, `crm.applications`; `platform.accounts`, `platform.transactions`, `platform.links`.

---

## Two-layer client RLS (the core pattern)

Client data (contacts/accounts/transactions + joins) is gated by:

```
(auth.can_read_all() AND tenant_id = ANY(auth.tenant_lineage()))   -- read-all staff, within firm
OR <scope predicate: id/contact_id/account_id ∈ auth.*_scope()>    -- scope-bound adviser/client
```

A client end-user has an **empty** `tenant_lineage()`, so the staff layer never fires for them —
they reach data only through the scope predicate. `0004` makes this a hard boundary: in the
**client portal** `tenant_lineage()` is empty and `can_read_all()` is false regardless of the
person's other roles, and their scope is only parties linked via `crm.contact_users` (never an
adviser book). The `contact_ids`/`account_ids`/`portal` claims are all minted server-side by
`auth.resolve_claims()`; absent claims fail closed.

---

## Not yet implemented (deferred)

Per `docs/data-architecture.md §8`: the wider platform catalog (`platform_tenants`,
`product_providers`, countries/currencies, advisers, terms, domains); the `integrations` schema
(providers/connections/sync_jobs/`transaction_expectations` — the `transactions.expectation_id`
FK is reserved); the money ledger + pensions domain; natural income; the remaining WorkOS mirror
tables (apps/organisation_domains/sessions); pgmq queues; the webhook-driven per-project
reconciler; inbound provider sync.
