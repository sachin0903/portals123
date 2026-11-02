# 5 · Admin — tenants, members, roles, operations, branding, audit

Portal: **admin**. Part of the [functional audit](README.md) — statuses: ✅ working · ⚠️ partial · 🎭 demo/mock · ❌ missing · 💥 broken

---

Legend: ✅ works end-to-end · ⚠️ partial/degraded · 🎭 fake/demo · ❌ missing · 💥 broken

---

## A. Tenant lifecycle

### Create firm (from_model cloning)

- **Status**: ⚠️
- **What it does today**: The dialogue at `apps/admin/src/routes/tenants/+page.svelte:137` posts `name`/`alias`/`from_model` to `?/createTenant` (`+page.server.ts:28`) → `POST /admin/tenants` (`apps/api/app/routers/admin.py:414`) → `provisioning.provision_tenant` (`apps/api/app/provisioning.py:113`). It inserts one `auth.tenants` row and, when `from_model`, calls `clone_model_roles` (`provisioning.py:58`) plus `seed_onboarding` (`provisioning.py:151`). **No WorkOS call is made** — deliberate under project-per-tenant. Cloning copies `auth.roles` (slug/name/portal/description) + `auth.role_permissions` by slug match, then bulk-grants `flows:*`/`cases:*` to any cloned role holding `documents:write` (`provisioning.py:91-103`).
- **Expected**: As ops staff I create a firm and get a working role set so I can immediately invite its first admin. As ops staff I want a blank firm when I intend to hand-build roles.
  - Alias collision returns a readable 409, not a 500.
  - `from_model=true` yields ≥1 platform role and ≥1 client role.
  - The response tells me how many roles were cloned.
  - Creation is atomic — a failed onboarding seed does not leave a half-built firm.
- **Gaps**:
  - The seeded model tenant is **blank** — `0001_auth_core.sql:631` inserts `('ten_model','model','Model Tenant', true)` with **no roles**. On a fresh install `from_model=true` clones **zero** roles, so the new firm cannot be invited into at all (`grant_portal_access` raises `LookupError("role ... does not exist")`, `provisioning.py:187`). `scripts/bootstrap_dev_tenant.py:142` even prints "(none configured -- see /admin/model)".
  - `alias` is `NOT NULL UNIQUE` (`0001_auth_core.sql:116`); a duplicate raises `IntegrityError` → uncaught → HTTP 500 with no message the operator can act on.
  - `clone_model_roles` returns `count(*) FROM auth.roles WHERE tenant_id = :t` (`provisioning.py:104-110`) — the tenant's _total_ roles, not the number cloned; the value is discarded anyway.
  - `workos_project_id` is in the API model (`admin.py:101`) but the create dialogue never offers it, so the tenant overview always renders "no WorkOS project" (`tenants/[id]/+page.svelte:50`).
  - No `parent_id` control, so the tenant hierarchy/lineage machinery is unreachable from the UI.

### Tenants list + client-side search

- **Status**: ✅
- **What it does today**: `GET /admin/tenants` (`admin.py:431`) returns every non-model firm with `user_count`, `has_environment`, `has_keys`, `has_orgs` computed as correlated EXISTS sub-selects. `tenants/+page.svelte:33` filters client-side on name+alias; `isReady` = all three flags.
- **Expected**: As ops staff I see every firm and can tell at a glance which are live. Search finds a firm by name or slug.
  - Ready/Setup-needed reflects the real environment state.
  - The list excludes the model tenant.
  - `tenants:write` gates the "New tenant" button.
- **Gaps**: No pagination or server-side search (fine at tens of firms); no sort; `has_keys` only checks `workos_api_key_ct IS NOT NULL`, so an environment with an api key but no cookie password reads "ready" while `list_environments.has_secrets` (`environments.py:104`) requires both — two different definitions of "keys set".

### Tenant overview checklist

- **Status**: ⚠️
- **What it does today**: `tenants/[id]/+page.server.ts:8-15` fetches members + environment in parallel and **swallows every non-OK response into `[]`/`null`**. The checklist (`+page.svelte:23-41`) derives five steps: firm created (hardcoded `true`), configure WorkOS (`!!env`), set keys (`env.has_secrets`), create portal orgs (both org ids), invite members (`members.length > 0`).
- **Expected**: As ops staff the checklist tells me exactly what is left before a firm can go live, and each incomplete step links to the fix.
  - A failed API call shows an error, not a false "not done".
  - The checklist includes every genuine precondition for go-live.
  - Readiness here matches the tenants-list "Ready" badge.
- **Gaps**:
  - **"Roles configured" is missing.** A firm cloned from an empty model shows 4/5 done, yet the Invite form has no roles to pick and every invite 409s. The one precondition that actually blocks onboarding is absent from the checklist.
  - A 403/502 on either fetch renders as "not configured / no members" — silently wrong. No `LoadErrorBanner` here, unlike the dashboard.
  - "Branding" is not a step even though it has a sidebar entry and is required for a white-label launch.
  - Readiness percentage counts the always-true "Firm created" step, so an untouched firm shows 20% rather than 0%.

### Register WorkOS environment

- **Status**: ⚠️
- **What it does today**: `tenants/[id]/environments/+page.svelte:95` posts to `?/saveConfiguration` → `PUT /admin/tenants/{id}/environment` (`admin.py:295`) → `environments.set_environment` (`environments.py:362`), an upsert on `(tenant_id, stage)` where `stage` is stamped from `settings.app_stage` — never supplied by the caller. Only `workos_client_id` is required; `issuer`/hosts are optional.
- **Expected**: As ops staff I paste the client id and portal hosts from the WorkOS dashboard and the shared portals immediately route that host to this project.
  - Saving is idempotent for the instance's stage.
  - The API's verification registry picks the change up without a restart.
  - A malformed client id is rejected before it can break sign-in.
- **Gaps**:
  - `saveConfiguration` does **not** call `/admin/environments/refresh` (only `setSecrets` does, `+page.server.ts:51`). Registering or changing a client id/issuer leaves `tenant_registry` stale until keys are next saved or the API restarts; tokens then fall through to the single configured environment (`auth.py:118-124`) and 401.
  - No validation that `workos_client_id` exists in WorkOS, and no "test connection" — a typo is only discovered when a user cannot sign in.
  - Hosts are free text with no uniqueness constraint check surfaced; `env_config_by_host` uses `.one_or_none()` (`environments.py:466`), so two tenants sharing a host raises `MultipleResultsFound` → 500 on every portal request for that host.
  - Only one environment per instance is editable; the multi-stage `list_environments` capability is not exposed.

### Set / rotate environment secrets (encryption)

- **Status**: ⚠️
- **What it does today**: `?/setSecrets` → `PUT /admin/environments/{id}/secrets` (`admin.py:319`) → `environments.set_environment_secrets` (`environments.py:412`) Fernet-encrypts both values via `crypto.encrypt` (`crypto.py:45`) with `SECRETS_MASTER_KEY` (`config.py:56`) and upserts `auth.environment_secrets`. The same handler then builds the env client, calls `ensure_portal_orgs`, and calls `push_roles_to_workos` (`admin.py:342-356`). The UI generates a 64-hex cookie password client-side and confirms rotation via `ConfirmDialog` (`+page.svelte:250`).
- **Expected**: As ops staff I paste the project's API key and a generated cookie password; keys are encrypted at rest, never echoed, and the portal orgs + roles land in WorkOS in the same step.
  - An unset/invalid master key gives a clear, actionable error.
  - A bad API key fails without half-writing state.
  - Rotation warns that live sessions break, and propagates within a bounded window.
- **Gaps**:
  - `crypto.SecretsNotConfigured` (master key unset or not a valid Fernet key) is **not caught** anywhere on this path → opaque HTTP 500. The single most likely misconfiguration produces the least useful error.
  - `WorkOSError` from `ensure_portal_orgs` / `push_roles_to_workos` is also uncaught in `set_environment_secrets` → 500 (contrast `admin.py:257`, `:646`, where the same error is mapped to 422). A rejected role slug therefore blocks _setting keys at all_.
  - Rotation consequences are only half-stated: the dialogue says "existing sessions will break" (`+page.svelte:253`) — true, because `packages/auth/src/environment.ts:100` caches `env-config` for 60 s and sealed cookies were sealed with the old password. It does not say that the **old ciphertext is overwritten irrecoverably** (no previous-key retention), so a mistyped key cannot be rolled back — only re-pasted from WorkOS.
  - There is no way to verify the stored key works after the fact (no health probe), and `has_secrets` only asserts ciphertext presence.
  - Rotating the master key itself has no re-encryption path — every environment secret becomes undecryptable (`InvalidToken`) with no tooling to migrate.

### Create portal orgs in WorkOS

- **Status**: ✅
- **What it does today**: `environments.ensure_portal_orgs` (`environments.py:254`) lists the project's orgs, matches `PORTAL_ORGS` by **name** ("Client Portal", "Platform"), creates any missing one, and records the ids on `auth.tenant_environments`. `ensure_operations_org` (`environments.py:226`) does the same for "Graphene Internal Operations" against the **core** project (`core_workos_client`, from settings, not a tenant key) and is triggered by `GET /admin/operations` (`admin.py:205`) and `GET /internal/operations-org`.
- **Expected**: As ops staff the two portal orgs exist exactly once per project, and re-running the step never duplicates them.
  - Idempotent against WorkOS, not just the DB.
  - The recorded ids feed the token→environment registry.
- **Gaps**: Matching by display name means an operator who renames the org in the WorkOS dashboard gets a _second_ org created on the next DB reset. `ensure_portal_orgs` pages the whole org list on every call. No UI to see or re-link the org ids beyond a "Orgs created/pending" badge.

### Refresh the environment registry

- **Status**: ✅
- **What it does today**: `POST /admin/environments/refresh` (`admin.py:359`) → `tenant_registry.refresh` = `load()` (`tenant_registry.py:69`), rebuilding both the issuer→env and org→env maps in process memory, deriving a missing issuer from the client id (`default_issuer`, `tenant_registry.py:40`).
- **Expected**: After registering an environment the shared API verifies its tokens without a restart.
  - Returns the count loaded.
  - Only reachable with `tenants:write`.
- **Gaps**: Per-process only — with more than one API replica, only the replica that served the request is refreshed; the others stay stale until restart. Not called after `saveConfiguration` (see above). No UI surface or feedback; the count is discarded by the SvelteKit action (`environments/+page.server.ts:51`).

### Tenant settings — rename / alias / website

- **Status**: ✅
- **What it does today**: `tenants/[id]/settings/+page.svelte:48` posts to `?/editTenant` → `PATCH /admin/tenants/{id}` (`admin.py:526`) → `provisioning.update_tenant` (`provisioning.py:485`), a `COALESCE`-guarded UPDATE of name/alias/website/enabled. Nothing is written to WorkOS — correct under project-per-tenant.
- **Expected**: As ops staff I correct a firm's name, slug or website and the change shows across the portal.
  - Only fields I changed are written.
  - A duplicate alias is rejected readably.
- **Gaps**: `.one()` on the UPDATE (`provisioning.py:519`) means a non-existent tenant is a 500, not a 404. A duplicate alias hits the unique index → uncaught → 500. Changing `alias` has no downstream effect (it is not a host or a WorkOS identifier), so the "stable slug" hint understates that it is purely cosmetic. `parent_id` cannot be edited.

### Enable / disable a firm (does disable block sign-in?)

- **Status**: 🎭
- **What it does today**: The Switch at `settings/+page.svelte:96` sets `enabled` and a `ConfirmDialog` (`:114`) states "Every member of {tenant.name} will be blocked from signing in until the firm is re-enabled." The value reaches `auth.tenants.enabled` via `update_tenant`. **Nothing reads it.** I traced every consumer: `auth.resolve_claims` (`0001_auth_core.sql:503-537`) resolves user + tenant + lineage and never touches `enabled`; no RLS policy references it (grep of `0001_auth_core.sql` finds only the column definition at line 140 and its index at 152); `packages/auth/src/auth.ts:96` authenticates straight against WorkOS; `GET /me/tenant` (`me.py:24`) does return `enabled` but **no portal calls it** (grep across `apps/*/src` returns nothing).
- **Expected**: As ops staff, disabling a firm immediately locks every one of its logins out of every portal, and existing sessions stop working.
  - A disabled tenant's members are refused at sign-in with a clear message.
  - Existing sessions for that tenant are invalidated or fail on the next API call.
  - The API refuses requests whose resolved tenant is disabled.
  - The audit trail records who disabled the firm and when.
- **Gaps**:
  - **The toggle is decorative.** A disabled firm's staff and clients sign in and use both portals exactly as before. The confirm dialogue asserts a security guarantee the system does not provide.
  - The same is true of `auth.users.enabled`: `deactivate_member` sets it false (`provisioning.py:615`) but `resolve_claims` only filters `deleted_at IS NULL` (`0001_auth_core.sql:515`). Only the WorkOS membership deactivation actually blocks anything.
  - No kill switch for sessions — nothing revokes WorkOS sessions on disable.
  - The dashboard's "Active firms" tile (`+page.svelte:110`) reports `enabled` as "all sign-in enabled", propagating the same false claim.

### Branding editor (S3 upload, theme tokens, versioning)

- **Status**: ⚠️
- **What it does today**: `tenants/[id]/branding/+page.server.ts:29` loads both portals via `GET /admin/tenants/{id}/branding?portal=…` (`admin.py:547` → `branding.get_branding`), and `?/save` writes both with `PUT` (`admin.py:558` → `branding.set_branding`, `branding.py:376`) into the single `auth.tenants.theme` jsonb as `Theme{v:2, client, platform}`. Uploads go browser → `?/upload&kind=…` → `POST /admin/tenants/{id}/branding/asset` (`admin.py:572`) → `storage.put_public_asset` (`storage.py:106`), content-hashed to `tenants/{id}/{kind}-{digest}.{ext}`.
- **Expected**: As ops staff I white-label a firm's two portals, upload its logos and favicon, and can see/revert what changed.
  - Every asset kind the editor offers actually uploads.
  - With no object storage configured I get a clear instruction, not a silent fallback.
  - Branding changes are versioned or at least audited.
- **Gaps**:
  - 💥 **`icon` uploads always fail.** The API allows `{"logo","logo-dark","icon","favicon"}` (`admin.py:173`) and `BrandingEditor` offers an icon upload (`packages/ui/src/lib/components/branding/branding-editor.svelte:205`, `:188`), but the SvelteKit action's allowlist is `new Set(['logo','logo-dark','favicon'])` (`branding/+page.server.ts:42`) → `fail(400, 'Unknown asset kind.')`. Same defect in `apps/platform/src/routes/branding/+page.server.ts:60`.
  - **Dead error path / silent data-URI fallback.** `admin.py:595` catches `storage.StorageUnavailable` to return "Object storage is not configured — paste a hosted https URL instead", but `put_public_asset` **never raises it** — with no bucket it base64-inlines the asset (`storage.py:119-121`) straight into `auth.tenants.theme`. Logos therefore end up embedded in a jsonb column with no operator warning.
  - **No versioning or history.** `set_branding` overwrites `theme` in place (`branding.py:387`). `Branding.v`/`Theme.v` are format discriminators, not revisions. There is no preview-vs-publish, no revert, and — because `auth.tenants` _is_ audit-attached — the only trace is a raw jsonb diff in `audit.log.detail`.
  - The firm `name` is written twice (once per portal, `branding/+page.server.ts:61-71`), so a partial failure leaves the second portal's branding unsaved while the name is already changed.
  - `?/upload` reads the whole file into the SvelteKit process then re-posts it; no client-side size guard against the API's `MAX_ASSET_BYTES`.

### Tenant deletion

- **Status**: ❌
- **What it does today**: Nothing. There is no `DELETE /admin/tenants/{id}` (the only `@router.delete` in `admin.py:653` is `revoke-member-portal`), no `provisioning.delete_tenant`, no soft-delete column on `auth.tenants`, and no UI control. The schema is ready for it — `auth.users.tenant_id`, `auth.tenant_environments.tenant_id` and `audit.log.tenant_id` all cascade (`0001_auth_core.sql:160`, `:174`) — but nothing invokes it.
- **Expected**: As ops staff I offboard a firm that has left the platform, with its data and WorkOS artefacts dealt with deliberately.
  - Deletion is two-step (disable, then archive/delete) and confirmed by typing the firm name.
  - The firm's WorkOS orgs/memberships are deactivated as part of the flow.
  - The audit trail survives the deletion.
  - An export/retention step runs before destruction (FCA record-keeping).
- **Gaps**: No offboarding path at all — not even an archive flag. `enabled=false` is the only lever and it does nothing (above). `audit.log.tenant_id` is `ON DELETE CASCADE`, so a naive future implementation would destroy the compliance trail along with the firm.

---

## B. Member management (`tenants/[id]/members`)

### Invite a member (WorkOS user → membership → reset token → Resend)

- **Status**: ⚠️
- **What it does today**: `?/addMember` (`members/+page.server.ts:32`) → `POST /admin/tenants/{id}/members` (`admin.py:607`) → `provisioning.grant_portal_access` (`provisioning.py:155`). Chain: resolve the tenant's own role (rejects unknown slugs, `:186`); resolve the portal org + client (`environments.portal_org_and_client`); reuse-or-create the WorkOS user with `email_verified=False` and no password (`:228-235`); `create_organization_membership(role=org-<slug>)`, falling back on `ConflictError` to update-role + reactivate (`:239-254`); if unverified, mint `reset_password` and build `{portal_base_url}/set-password?token=…` (`:261-264`); send via Resend; upsert `auth.users` (`enabled = verified`, `invitation_sent_at = now()`) and `auth.organisation_memberships` (`status = 'pending'`).
- **Expected**: As ops staff I invite a person by email and role; they receive a link to set a password on the firm's own portal, and I can copy that link if email fails.
  - With Resend unconfigured the invite still succeeds and the link is visible.
  - The invite is idempotent per (tenant, email).
  - A firm with no roles gives a clear "configure roles first" error.
- **Gaps**:
  - **Resend unconfigured degrades correctly and the link is surfaced** — `email.send_email` returns `False` and logs a warning without raising (`email.py:33-35`), and `members/+page.svelte:172-179` renders a read-only, copyable "Invitation link". ✅ on that specific concern. _But_: the toast says "Invitation sent." (`members/+page.server.ts` → `+page.svelte:131`) regardless of whether the email actually went out — `send_email`'s boolean return is discarded at `provisioning.py:269`. The operator cannot tell a delivered invite from a silently skipped one.
  - If `portal_base_url` is null (hosts not registered) the link falls back to WorkOS's own `password_reset_url` (`provisioning.py:264`) — an AuthKit-hosted page, breaking the white-label promise, with no warning.
  - Email is a bare `str` on `CreateMember` (`admin.py:117`) — no `EmailStr` validation server-side; the UI relies on `type="email"` only.
  - `invitation_expires_at` / `invitation_id` / `invitation_accepted_at` columns exist but are never written, so invite expiry is invisible.
  - The invite role picker lists **all** the firm's roles across both portals (`members/+page.svelte:50-52`); if the firm has zero roles the select is empty with no explanatory copy (contrast the ops page, which does say "Create an operations role first", `operations/users/+page.svelte:107`).
  - No bulk/CSV invite; no re-check that the person isn't already a member (handled server-side by upsert, but the UI gives no feedback that it was a no-op).

### Grant portal access

- **Status**: ⚠️
- **What it does today**: `?/grantPortal` → `PUT /admin/tenants/{id}/members/{user}/portals/{portal}` (`admin.py:630`) → the same `grant_portal_access` with `user_id` set, so it reuses the existing WorkOS user, adds/updates the org membership, and reactivates an inactive one (`provisioning.py:243-254`).
- **Expected**: As ops staff I give an existing person the other portal, or restore access I previously revoked, choosing their role in it.
  - Granting a revoked portal reactivates it.
  - The role must belong to that portal.
- **Gaps**:
  - **The re-grant path is unreachable from the UI.** `revoke` sets the mirror row to `status='inactive'` rather than deleting it (`provisioning.py:423-429`), so `portalOf(m, portal)` still finds it (`members/+page.svelte:56`) and the cell renders the _held_ branch — role select + Revoke — never a Grant button (`:223-314`). To restore one portal you must use the whole-user Reactivate, which reactivates **every** portal.
  - Granting a portal to a deactivated person does not clear `auth.users.enabled=false`, leaving an "active membership on a suspended user" state that the members table renders as both "Suspended" and "Active".

### Revoke portal access

- **Status**: ⚠️
- **What it does today**: `?/revokePortal` → `DELETE …/portals/{portal}` (`admin.py:653`) → `provisioning.revoke_portal_access` (`provisioning.py:410`): resolves the WorkOS membership by listing the user's memberships and matching the tenant's org id (`_workos_membership`, `:541`), calls `deactivate_organization_membership`, and sets the mirror row inactive. Confirmed by `ConfirmDialog` (`members/+page.svelte:404`).
- **Expected**: As ops staff I remove one portal without touching the person's other access, and it takes effect on their next sign-in.
  - The user's other portal survives.
  - Their live session for the revoked portal stops working.
- **Gaps**: WorkOS membership deactivation does not revoke an already-issued access token or sealed session; nothing invalidates the session, so revoked access persists until token expiry. `_workos_membership` pages the user's entire membership list on every call (one WorkOS round-trip per action). The revoked state renders as a `danger`-toned badge with the raw status text because `STATUS_TONE` only maps `active`/`pending` (`members/+page.svelte:60`).

### Change a member's role within a portal

- **Status**: ✅
- **What it does today**: `?/setRole` → `PATCH …/portals/{portal}` (`admin.py:677`) → `provisioning.set_member_role` (`provisioning.py:565`): `update_organization_membership(role=org-<slug>)` then updates `role_id` on the mirror row.
- **Expected**: As ops staff I change someone's role and their permissions change on their next token refresh.
  - WorkOS and the mirror stay in step.
  - An unknown slug is refused.
- **Gaps**: The role must already exist **in WorkOS** — if `push_roles_to_workos` never ran (keys set before roles were created, or a role created while the model tenant was the target) WorkOS 422s and the endpoint surfaces it as 422 with a raw provider message. The mirror `UPDATE` uses a scalar sub-select for `role_id` (`provisioning.py:583`); an unknown slug silently writes `NULL` rather than failing, because the WorkOS call is what validates. Permissions only take effect after the user's token refreshes — no forced re-issue and no UI note.

### Deactivate / reactivate a member

- **Status**: ⚠️
- **What it does today**: `?/deactivate` → `POST …/{user}/deactivate` (`admin.py:731`) → `provisioning.deactivate_member` (`provisioning.py:591`): loops every portal the user holds, deactivates each WorkOS membership (swallowing `LookupError`), sets **all** mirror rows to `inactive`, and sets `auth.users.enabled = false`. Reactivate (`:622`) is the exact inverse, setting every membership to `active` and `enabled = true`.
- **Expected**: As ops staff I suspend a person across the whole firm in one action and restore them later to the access they had.
  - Reactivation restores the previous per-portal state, not a blanket grant.
  - A person who never set a password comes back as pending, not active.
- **Gaps**:
  - **Reactivate loses revocation state.** It sets every membership `active`, so a portal deliberately revoked _before_ the suspension is silently restored (`provisioning.py:637-643`). There is no record of the pre-suspension state to restore.
  - Reactivate sets `status='active'` unconditionally, even for a user whose email is still unverified — contradicting the `verified ? active : pending` rule used everywhere else (`provisioning.py:740`, `events.py:184`).
  - `auth.users.enabled` is set but never enforced at sign-in (see the Enable/disable finding), so "suspended" relies entirely on the WorkOS membership deactivation succeeding; the `except LookupError: continue` (`:606`) means a user with no `workos_user_id` is marked suspended locally while remaining fully able to sign in.
  - No reason/note captured for a suspension.

### Resend invite

- **Status**: ⚠️
- **What it does today**: `?/resendInvite` → `POST …/{user}/resend-invite` (`admin.py:769`) → `provisioning.resend_member_invite` (`provisioning.py:433`): picks the user's pending membership (`ORDER BY (m.status='pending') DESC LIMIT 1`), mints a fresh `reset_password` token, rebuilds the set-password URL, re-sends via Resend, and stamps `invitation_sent_at = now()`. Returns `invitation_url`.
- **Expected**: As ops staff I re-send a stale invite and can copy the fresh link if the email doesn't arrive.
  - The new link is shown, not just confirmed.
  - The link targets the portal the person is pending on.
- **Gaps**:
  - **The fresh link is thrown away by the UI.** The action returns `invitationUrl` (`members/+page.server.ts:163`) but `members/+page.svelte:36-39` deliberately only surfaces the _invite_ form's URL — a resend shows only the toast "Invitation link refreshed." So when Resend is unconfigured, resend is a no-op with a success message and no recoverable link. This is the single worst manifestation of the email-optional design.
  - The endpoint never passes `portal`, so for a multi-portal person the link is built for whichever membership sorts first — potentially the wrong portal.
  - The Resend button only appears when some portal is `pending` (`anyPending`, `members/+page.svelte:58`), so a user stuck `active`-but-unverified (possible via reactivate, above) cannot be re-invited.
  - No rate limit on resend; each call mints a new WorkOS reset token, invalidating the previous link.
  - **`operations/users` has no resend action at all** — an ops staff invite that fails to deliver has no recovery path beyond re-inviting.

### Pending → active sync (`email_verified` → `enabled`)

- **Status**: ⚠️
- **What it does today**: Three paths write the mirror status, all keyed on `email_verified`: `sync_tenant` (`provisioning.py:692`, manual "Sync from WorkOS" button, `members/+page.svelte:104`), `events._upsert_user`/`_upsert_membership` (`events.py:108`, `:157`, driven by the poller), and the invite itself. The rule is `inactive if WorkOS status inactive else (active if verified else pending)` (`provisioning.py:740`, `events.py:184`). Setting a password verifies the email in WorkOS — `admin/src/routes/set-password/+page.server.ts:21` calls `confirmPasswordReset` — and **writes nothing locally**.
- **Expected**: When someone accepts an invite and sets a password, the admin portal shows them active within a minute, without an operator pressing anything.
  - The transition is automatic.
  - Until it happens the UI does not mislabel them.
- **Gaps**:
  - **No push, no automatic pull by default.** `set-password` doesn't touch the mirror, and `WORKOS_SYNC_POLL_SECONDS` defaults to `0` (`config.py:116`) _and_ is defaulted to `0` in deployment (`docker-compose.yaml:55`) — so the poller is off. The only mechanism is an operator clicking "Sync from WorkOS" per tenant.
  - Consequence in the UI: a person who has fully onboarded still shows `enabled=false` → the members table renders a red **"Suspended"** badge (`members/+page.svelte:214-216`) and their portal chip reads "Pending". The overview/dashboard counts are correspondingly stale.
  - `last_sign_in` and `invitation_accepted_at` are **never written** anywhere in the codebase (grep confirms only reads). `users/[id]/+page.server.ts:15` types `last_sign_in` but it is permanently null.
  - The sync is O(all memberships) per tenant with no cursor; it re-upserts every row on every click.

---

## C. Roles & permissions

### RolesEditor — create a role

- **Status**: ⚠️
- **What it does today**: `lib/RolesEditor.svelte:277` posts name/slug/portal to the host page's `?/createRole`; the tenant page (`tenants/[id]/roles/+page.server.ts:18`) and the model page (`model/+page.server.ts:29`) both restrict portal to `client|platform`, the ops page hardcodes `operations` (`operations/roles/+page.server.ts:48`). All hit `POST /admin/tenants/{id}/roles` (`admin.py:235`) → `rbac.create_org_role` (`rbac.py:95`): for a real tenant, `create_organization_role` on the portal's org with an `org-` prefix (adopting an existing WorkOS role on `UnprocessableEntityError`, `:121-129`), then the mirror upsert + `_mirror_org_role_permissions`. Mirror-only for the model tenant.
- **Expected**: As ops staff I add a role for a firm and it exists in both WorkOS and the mirror, ready to assign.
  - I can also rename, describe and delete roles.
  - A slug already used in WorkOS is adopted, not fatal.
- **Gaps**: **No delete and no rename** — `auth.roles` has no delete endpoint anywhere, so a mistyped role is permanent (and `DO UPDATE` on conflict means re-creating with the same slug is the only edit path, reachable only by re-submitting the create dialogue). The `description` field is accepted by the API (`admin.py:141`) but the create dialogue has no input for it, so descriptions are always null. No duplicate-slug pre-check client-side. The editor's portal segmented control is derived from a `portals` prop, but the _server_ actions independently re-validate — the two lists can drift (the ops page's editor passes `['operations']`, the tenant page's server rejects `operations`).

### RolesEditor — toggle permissions and save

- **Status**: ⚠️
- **What it does today**: `RolesEditor.svelte:213` toggles a local `draft` Set; a sticky save bar appears when dirty (`:237`) and posts the full permission list to `?/setRolePermissions` → `PUT /admin/tenants/{id}/roles/{slug}/permissions` (`admin.py:370`) → `rbac.set_org_role_permissions` (`rbac.py:162`): resolves the role's portal, `ensure_workos_permissions` creates any environment-level permission missing from the project (`environments.py:62`), `set_organization_role_permissions` sets the exact set, then `_mirror_org_role_permissions` deletes and re-inserts the mirror grants. `push_roles_to_workos` (`environments.py:295`) is the bulk equivalent, run when keys are set and on `sync_tenant(push_roles=True)`.
- **Expected**: As ops staff I flip a firm's role permissions — including `contacts:read_all`, the "see all vs own book" lever — and the change is enforced on the next token and fully evidenced.
  - Permission changes are audited.
  - Users' effective permissions update predictably.
- **Gaps**:
  - **Permission-grant changes are NOT audited.** `audit.attach` covers `auth.roles` and `auth.organisation_memberships` but **not `auth.role_permissions`** (`0001_auth_core.sql:799-802`; full attached-table list confirmed by grep across all migration SQL). Since `_mirror_org_role_permissions` deletes and re-inserts grants (`rbac.py:76-92`), the most security-sensitive configuration action in the product — granting a role firm-wide client visibility — leaves **no trace whatsoever** in `audit.log`. `auth.permissions` is likewise unattached.
  - The permission list is filtered to the selected portal (`RolesEditor.svelte:72`), so an `operations` permission can never be granted to a platform role — sensible, but it also means the editor silently hides permissions that exist in the catalogue for other portals with no explanation.
  - No indication of when a change takes effect (token refresh), and no "who holds this role" count, so an operator cannot see the blast radius before saving.
  - `push_roles_to_workos` calls `env_workos_client` directly (`environments.py:301`) rather than `client_for_env`, so it **cannot** use the core-project fallback — see the operations-staff finding below.

### Model tenant (`/model`)

- **Status**: ⚠️
- **What it does today**: `GET /admin/model` (`admin.py:184`) returns the single `is_model` tenant; `model/+page.server.ts:18` then reuses the ordinary `/admin/tenants/{id}/roles` endpoints against it. `rbac._is_model_tenant` (`rbac.py:17`) makes every write mirror-only — no WorkOS project. The page carries an accurate warning: "Changes here only affect firms created afterwards — never existing ones." (`model/+page.svelte:16-21`).
- **Expected**: As ops staff I curate the role preset once, and every subsequently created firm inherits a working set of roles and permissions.
  - The preset ships with sensible defaults.
  - The page states clearly that it is not retroactive.
- **Gaps**:
  - **It ships empty** (`0001_auth_core.sql:631` inserts the tenant with no roles), so the "preset" is a preset of nothing until an operator hand-builds it. This is the root cause of the create-firm gap.
  - No "apply to existing firms" / backfill action, and no way to see which firms were cloned from which preset generation.
  - The model tenant cannot hold `operations` roles (both the UI and `model/+page.server.ts:37` restrict to client/platform), which is correct but undocumented in the page copy.
  - Editing the model can never fail loudly against WorkOS, so a slug that WorkOS would reject is only discovered on the _next_ firm's `push_roles_to_workos`.

### Operations staff (`/operations/users`)

- **Status**: ⚠️
- **What it does today**: `operations/users/+page.server.ts:31` resolves the ops tenant via `GET /admin/operations` (`admin.py:205` → `environments.operations_tenant_id`, matching `settings.workos_client_id` against `auth.tenant_environments`, plus `ensure_operations_org`), then drives the _same_ tenant member endpoints filtered to `portal='operations'` and flattened (`toOpsMember`, `:17`). Invite/changeRole/deactivate/reactivate/sync mirror the tenant members page. `admin.py:61-89` picks the gate by target tenant, so these calls require `operations:*` rather than `tenants:*`.
- **Expected**: As Graphene ops I add and manage internal staff, assign them ops roles, and suspend leavers.
  - Ops staff live in the core project's Internal Operations org.
  - The same invite/deactivate guarantees as firm members.
- **Gaps**:
  - 💥 **"Sync from WorkOS" is likely broken for the ops tenant.** `?/sync` (`operations/users/+page.server.ts:135`) → `sync_tenant(push_roles=True)` → `push_roles_to_workos` → `env_workos_client` (`environments.py:301`), which raises `LookupError("environment has no stored WorkOS api key")` whenever the core environment relies on `settings.workos_api_key` instead of a stored ciphertext — because it bypasses `client_for_env`'s core fallback (`environments.py:145-156`). Surfaces as a 409. Every other ops path (`portal_org_and_client`, `resend_member_invite`, `events.consume`) correctly uses `client_for_env`.
  - **No resend-invite** on this page, so an undelivered ops invite is unrecoverable (see above).
  - `operations` roles are silently skipped by `push_roles_to_workos` (`org_by_portal` only maps client/platform, `environments.py:315`), so ops roles reach WorkOS only through `create_org_role`/`set_org_role_permissions` — never through the bulk reconcile.
  - `ensure_portal_orgs` runs against the core project too, creating stray "Client Portal"/"Platform" orgs inside Graphene Ops when its keys are set.
  - No self-lockout guard: an ops user can deactivate themselves or strip `system` from their own role.

### Operations roles (`/operations/roles`)

- **Status**: ✅
- **What it does today**: `operations/roles/+page.server.ts:26-30` loads the ops tenant's roles and the permission catalogue, filters both to `portal='operations'`, and renders `RolesEditor` with `portals={['operations']}` (hiding the segmented control). Creates land in the core project's ops org via `portal_org_and_client(portal='operations')` (`environments.py:209-212`).
- **Expected**: As Graphene ops I define internal roles and which ops permissions each carries, including the superuser.
  - The `system` permission is visibly the full-access grant.
  - Read-only viewers see roles without mutation controls.
- **Gaps**: The `system` permission is a plain toggle among the rest — the page header warns about it in prose (`operations/roles/+page.svelte:12`) but the switch itself carries no destructive styling or confirmation. Same missing delete/rename as the general editor. `canManage` derives from `operations:write` client-side only.

### Permission registry sync on startup

- **Status**: ✅
- **What it does today**: `main.py:50-51` runs `permissions.sync_permissions` inside a `system_transaction` on every boot; it upserts all 26 declared `Permission`s into `auth.permissions`, refreshing name/resource/action/portal/description, never deleting (`permissions.py:267-290`). The same registry generates the TS constants consumed by `apps/admin/src/lib/permissions.ts` via `scripts/generate_permissions_ts.py`, and `satisfies` implements write⇒read + `system` bypass identically in Python (`permissions.py:257`) and TS.
- **Expected**: A permission is declared once and appears in the DB catalogue, the API enforcement layer and the portal's `can()` with no drift.
  - Idempotent on every boot.
  - CI fails on TS drift.
- **Gaps**: Never prunes, so a removed permission lingers in the catalogue and in the RolesEditor's toggle list indefinitely with no "deprecated" marker. `GET /admin/permissions` (`admin.py:176`) has **no `requires(...)` dependency** — the only admin read endpoint with no granular gate. `scripts/delta_operations_rbac.sql:17` still references the pre-taxonomy slug `model:manage` (now `model:write`), and `db.py:157` documents the old `tenants:manage`/`operations:manage` names — stale but harmless.

### Reconcile roles from WorkOS (`/roles/sync`)

- **Status**: ❌ (endpoint exists, no UI)
- **What it does today**: `POST /admin/tenants/{id}/roles/sync` (`admin.py:262`) → `rbac.sync_org_roles_from_workos` (`rbac.py:190`) walks both portal orgs, pulls every `OrganizationRole`, strips the `org-` prefix, and upserts role + permissions into the mirror. Its docstring names the exact failure it fixes: a WorkOS role created but never mirrored, which then 422s "already in use" forever.
- **Expected**: When the mirror and WorkOS disagree about roles, ops staff can reconcile from the UI.
- **Gaps**: **Nothing in `apps/admin` calls it** (grep for `roles/sync` returns only the API). The documented escape hatch for a known, recurring failure mode is reachable only with curl. It is also a no-op for the model tenant and skips a portal whose org is unset.

---

## D. Users (cross-tenant directory)

### Global users list

- **Status**: ✅
- **What it does today**: `users/+page.server.ts:24` → `GET /admin/users` (`admin.py:392`) → `directory.list_users` (`directory.py:7`): every `auth.users` row with a `count(DISTINCT m.tenant_id)` membership tally, ordered by email. `users/+page.svelte:21` filters client-side on name+email.
- **Expected**: As ops staff I find any login across the estate and jump to its detail.
  - Search matches name or email.
  - I can see which firm(s) a person belongs to.
- **Gaps**: No firm column — with per-tenant email uniqueness, the same address legitimately appears several times and the list cannot distinguish them (only the membership _count_ is shown). No pagination or server-side search: the whole directory is loaded and filtered in the browser, which will not hold as client logins accumulate. `deleted_at` is not filtered, so soft-deleted users would appear. No status filter, no export.

### User detail — memberships, role change, deactivate

- **Status**: 💥
- **What it does today**: `users/[id]/+page.server.ts:41` → `GET /admin/users/{id}` (`admin.py:401`) → `directory.user_with_memberships` (`directory.py:28`), which returns **one row per (tenant, portal)** including `m.portal`.
- **Expected**: As ops staff I see everywhere a person has access, change their role in a firm, and revoke a membership.
  - Role options come from that firm's actual roles.
  - A person with both Client and Platform access in one firm renders both rows.
  - The role change actually applies.
- **Gaps**:
  - **The role change targets an endpoint that does not exist.** `?/changeRole` posts `PATCH ${apiUrl()}/admin/tenants/{tenantId}/members/{userId}` (`users/[id]/+page.server.ts:59`). The API has no such route — role changes live at `PATCH …/members/{user_id}/portals/{portal}` (`admin.py:677`), confirmed against the generated client (`packages/api-client/src/types.gen.ts:3739`). Every "Set" click returns 404 and surfaces a generic failure toast. **The control has never worked.**
  - **The hardcoded ROLES array is still live, in both layers.** `users/[id]/+page.server.ts:36` (`['client','adviser','paraplanner','compliance','firm_admin']`) is used to _validate_ the submitted slug, and `+page.svelte:26` duplicates it for the dropdown, with the TODO acknowledging it at `:23-25`. A third copy is exported as `ROLES` from `lib/api.ts:29`, unused elsewhere. Since roles are now per-tenant with arbitrary slugs, the dropdown shows five slugs that may not exist in the target firm and rejects the ones that do. Even if the URL were fixed, the validation would reject legitimate roles.
  - **The membership list crashes for dual-portal users.** `{#each data.memberships as m (m.tenant_id)}` (`+page.svelte:82`) keys on `tenant_id`, but the API returns one row per portal — a person holding Client _and_ Platform in one firm produces duplicate keys (Svelte `each_key_duplicate`). The `Membership` type (`+page.server.ts:17-23`) omits `portal` entirely, so the page cannot even display which portal a row refers to.
  - `?/deactivate` (`:71`) correctly hits `POST …/{user}/deactivate` and works — but it is labelled "Deactivate this membership" while it actually suspends the person across **every** portal in that firm and sets `enabled=false` (`provisioning.py:591`). Misleading confirm copy.
  - No reactivate on this page, no portal grant/revoke, no `last_sign_in` display (the field is fetched but never rendered, and is always null anyway).

---

## E. Audit log

### Audit trail view

- **Status**: ⚠️
- **What it does today**: `audit/+page.server.ts:24` fetches `GET /admin/audit?limit=100` (`admin.py:466`) — a keyset query over `audit.log` joined to actor and firm, `ORDER BY l.id DESC`, supporting `action` and `before_id` params, `limit` clamped to 200. Rows come from the DML spine: `audit.record()` (`0001_auth_core.sql:723`) is a `SECURITY DEFINER` AFTER-trigger owned by `service_role`, stamping the affected row's `tenant_id` (falling back to the actor's), `auth.uid()`, `<schema>.<table>.<op>`, an integer `entity_id` where the PK is int-shaped, and a `detail` jsonb carrying the inserted row / changed-column diff, with `key_hash`, `credentials_ref`, `workos_api_key_ct`, `cookie_password_ct` redacted (`:733`). `attach` is applied to 35 tables. The actor resolves because `admin_session` publishes claims to `request.jwt.claims` (`db.py:182-186`). The table is append-only to the app role (`GRANT SELECT, INSERT` only, `:710`).
- **Expected**: As ops staff (and for FCA evidence) I can see who changed what and when, filter to an action, actor, firm or date range, page back through history, and export it.
  - Every security-relevant write is captured.
  - Filtering and pagination happen server-side.
  - The trail is tamper-evident and exportable.
- **Gaps**:
  - **Hard 100-row cap with no pagination.** The UI hardcodes `limit=100` and never sends `before_id`, so the trail is permanently truncated to the newest 100 events. The search box filters _only those 100 rows_ client-side (`audit/+page.svelte:19-27`), which reads as a search over the log but is not.
  - **The server-side `action` filter is never used**, and there is no actor, tenant, entity-type or **date-range** filter at all — none exist server-side either.
  - **No export.** No CSV/JSON download, which is the core requirement for a compliance trail.
  - **`detail` is fetched but never rendered** — the audit page has no detail column or expansion, so the actual before/after values (the substance of the evidence) are invisible in the UI.
  - **Coverage holes** (from the full `audit.attach` list): `auth.role_permissions` and `auth.permissions` are unattached → **role permission grants are unaudited** (the single most sensitive admin action). `documents.render_jobs` is deliberately excluded. Nothing captures **reads** (no view/access log for the cross-tenant user directory), **authentication events** (sign-in, MFA, failed logins, password sets — WorkOS auth events are explicitly ignored by `events.EVENT_TYPES`, `events.py:26-33`), or **WorkOS-side actions** (org/membership/role writes made by the admin portal appear only as their mirror consequences; a WorkOS call that succeeds while the mirror write rolls back leaves _no_ audit row).
  - `entity_id` is bigint, so uuid-PK tables (`auth.users`, `documents.documents`) always log `entity_id = NULL` (`0005_documents_storage.sql:73-74`); the "Target" column then shows a bare table name.
  - Actions from the poller/background paths run with no claims, so `user_id` is null and the row shows "—" with no way to distinguish "system" from "unknown actor".
  - No retention policy, no integrity check (hash chain / signed export) despite the append-only framing.

---

## F. WorkOS events poller

### Background events consumer

- **Status**: ⚠️
- **What it does today**: `poller.run_forever` (`poller.py:44`) returns immediately when `settings.workos_sync_poll_seconds <= 0`; otherwise it loops every N seconds, listing every `auth.tenant_environments` id and calling `events.consume` per environment in its own transaction, logging and continuing on failure (`:33-41`). `events.consume` (`events.py:37`) reads the per-environment `events_cursor`; on first run it does a full `sync_tenant(push_roles=False)` backfill then seeds the cursor to the newest event; thereafter it pages `list_events(events=EVENT_TYPES, after=cursor, order='asc', limit=100)`, applies each, and persists the cursor per page. It consumes six types only: `user.created/updated/deleted`, `organization_membership.created/updated/deleted` (`events.py:26-33`). `user.deleted` sets `enabled=false`; membership delete removes the mirror row; upserts match on `workos_user_id` OR `(tenant_id, email)`.
- **Expected**: The mirror tracks WorkOS within a minute without operator intervention, and a poller outage is visible and self-healing.
  - Enabled by default in every deployment.
  - Failures are alertable.
  - Nothing drifts silently while it is down.
- **Gaps**:
  - **Off by default, in code and in deployment.** `config.py:116` defaults to `0` and `docker-compose.yaml:55` sets `${WORKOS_SYNC_POLL_SECONDS:-0}`. Unless someone sets it explicitly, **there is no automatic reconcile at all** — which is what makes the pending→active drift, the "Suspended" mislabelling and the manual per-tenant Sync button the actual operating model.
  - **What drifts when it is down**: invite acceptance (`email_verified` → `enabled`/`active`), name/avatar/email changes, WorkOS-side membership adds/removes/role changes, and user deletions. Everything the admin portal itself writes is dual-written, so drift comes from WorkOS-side and portal-side (password set) changes only — but that includes the entire onboarding completion signal.
  - **No health surface.** No heartbeat table, no last-run timestamp, no per-environment cursor age, nothing in the admin UI. An operator cannot tell whether the poller is running, stalled, or has been failing for a week — only `logger.exception` in the container log.
  - Sequential per-environment loop with no concurrency and no jitter; a slow WorkOS project delays every later one.
  - The seed path (`cursor IS NULL`) runs a full `sync_tenant` **inside a single transaction** for every environment on first tick — unbounded work with no batching.
  - A single poisonous event fails the whole environment's transaction, so the cursor never advances and the same event is retried forever (no dead-letter, no skip).
  - Role/permission events are deliberately ignored ("push-only"), so a role edited directly in the WorkOS dashboard never reaches the mirror — the one thing `/roles/sync` would fix, and it has no UI.
  - `_portal_for_org` defaults unknown orgs to `'platform'` (`events.py:200`), so a membership in an unrecognised org silently lands as staff access.

---

## G. Internal router + `INTERNAL_API_TOKEN`

### Service-to-service internal endpoints

- **Status**: ✅
- **What it does today**: `apps/api/app/routers/internal.py` exposes three routes under `/internal`, `include_in_schema=False`, each gated by `require_internal_token` (`:30`) — a constant-time `hmac.compare_digest` that returns **404** (not 401/403) when the token is missing or wrong, so the endpoints' existence is not advertised; an unset `INTERNAL_API_TOKEN` disables them entirely. They run on a dedicated `internal_session` (`:38`) with `SET LOCAL ROLE service_role` and no request principal. `GET /env-config?host=` returns the decrypted per-host WorkOS config (`environments.env_config_by_host`, `:438`) — client id, issuer, **plaintext api key and cookie password**, the portal's org id and its branding. `GET /operations-org` find-or-creates the Internal Operations org. `POST /forgot-password` mints a reset link and emails it, always returning `{ok: true}` and swallowing every exception so it cannot enumerate accounts (`:98-106`).
- **Who calls them**: the three portals, not a deploy pipeline. `packages/auth/src/environment.ts:89` `resolveEnvironment` (60 s TTL cache, negative-caches 404s) is used by every portal's `hooks.server.ts` via `resolveAuth`; `apps/admin/src/lib/server/workos.ts:23` caches the ops org id **forever** in a module-level variable; `requestPasswordReset` (`environment.ts:129`) drives the forgot-password pages. `docker-compose.yaml` injects `INTERNAL_API_TOKEN` into the API and all three portal services (lines 39, 186, 229, 271).
- **Expected**: The API is the sole holder of `SECRETS_MASTER_KEY`; portals fetch what they need over an authenticated internal channel and never touch the DB.
  - The token is required, compared in constant time, and its absence disables the routes.
  - Secrets never reach a browser.
  - Config changes propagate within a bounded window.
- **Gaps**: A single shared static token for all callers — no per-service tokens, no rotation story, no expiry; rotating it requires simultaneous redeploys. Plaintext api keys and cookie passwords cross the wire in a JSON body, so TLS/network isolation is doing all the confidentiality work (the docstring's "deploy with these paths reachable only on the internal network" is the only control, unenforced in code). `opsOrgId` is cached for the process lifetime with no invalidation. `/forgot-password` swallows _all_ exceptions including genuine send failures, so a broken Resend config is indistinguishable from an unknown email, and there is no rate limit on the endpoint.

---

## H. Dashboard + SearchBar

### Dashboard

- **Status**: ✅
- **What it does today**: `+page.server.ts:22` redirects unauthenticated users, then falls through the permission ladder (`tenants:read` → `audit:read` → `operations:read` → `model:read`) so a scoped ops user lands somewhere useful rather than a 403 (`:26-31`). It fetches `/admin/tenants` with explicit handling for 403 and unreachable-API, surfaced via `LoadErrorBanner` (`+page.svelte:60`). All figures are derived from real rows: firms, summed `user_count`, `enabled` count, readiness percentage and a "Needs setup" list with per-firm missing steps (`:39-45`).
- **Expected**: As ops staff my landing page tells me the estate's size, what needs attention, and links straight to it.
  - Every number is real and links to its screen.
  - API failure is visible.
- **Gaps**: "Users across every firm" sums `user_count`, which counts `auth.users` rows — under per-tenant email uniqueness the same human is counted once per firm, so the headline is logins, not people. The "Active firms" tile's "all sign-in enabled" sub-label repeats the false `enabled` guarantee. No audit/activity feed and no poller/health indicator, so the two things most likely to be silently broken are invisible here.

### SearchBar (global search)

- **Status**: 🎭
- **What it does today**: `lib/SearchBar.svelte:8-14` holds a hardcoded array — Acme Advisers, Globex Wealth, Initech Financial, Stark Capital, Wayne Private — with the comment "Demo tenants for the cross-tenant search (wired to real org data later)". Every `Command.Item` calls `select('/tenants')` (`:40`), so whichever fictional firm you pick navigates to the same list page. It is mounted in the app shell on every authenticated route (`+layout.svelte:126-128`).
- **Expected**: As ops staff I press the search field (ideally ⌘K), type a firm, person or email, and jump straight to that record.
  - Results come from `/admin/tenants` and `/admin/users`.
  - Selecting a result navigates to its detail page.
  - Grouped results (Firms / People) with keyboard navigation.
- **Gaps**: Entirely fake — five invented firms shown to an operator on every screen of a production internal tool, which will read as real data. No API call, no users in scope, no deep links, no keyboard shortcut binding. The per-page search inputs on Tenants, Users and Audit are all separate client-side filters; there is no global search anywhere.

---

## Domain summary

| #   | Function                        | Status | One-line verdict                                                                      |
| --- | ------------------------------- | ------ | ------------------------------------------------------------------------------------- |
| 1   | Create firm (from_model)        | ⚠️     | Works, but the model ships empty so clones yield no roles; alias clash → 500          |
| 2   | Tenants list + search           | ✅     | Real data, real status flags; client-side search only                                 |
| 3   | Tenant overview checklist       | ⚠️     | Omits "roles configured"; swallows API errors as "not done"                           |
| 4   | Register WorkOS environment     | ⚠️     | No registry refresh on save; no client-id validation; duplicate host → 500            |
| 5   | Set/rotate environment keys     | ⚠️     | Fernet encryption sound; missing master key or WorkOS error → opaque 500; no rollback |
| 6   | Create portal orgs              | ✅     | Idempotent against WorkOS by org name                                                 |
| 7   | Refresh env registry            | ✅     | Works, but per-process only (stale on other replicas)                                 |
| 8   | Settings — rename/alias/website | ✅     | COALESCE update; 404s surface as 500                                                  |
| 9   | Enable / disable a firm         | 🎭     | **Nothing reads `tenants.enabled`** — the toggle blocks nothing                       |
| 10  | Branding editor                 | ⚠️     | `icon` upload 400s; no S3 → silent base64 into jsonb; no versioning                   |
| 11  | Tenant deletion                 | ❌     | Does not exist in any form                                                            |
| 12  | Invite member                   | ⚠️     | Full chain works; link surfaced; but "Invitation sent" lies when Resend is off        |
| 13  | Grant portal access             | ⚠️     | Endpoint fine; **UI offers no Grant control after a revoke**                          |
| 14  | Revoke portal access            | ⚠️     | Deactivates WorkOS membership; live sessions survive                                  |
| 15  | Change role in portal           | ✅     | Dual-writes WorkOS + mirror                                                           |
| 16  | Deactivate / reactivate         | ⚠️     | Reactivate blanket-restores revoked portals and forces `active`                       |
| 17  | Resend invite                   | ⚠️     | **Fresh link returned by the API but never shown**; absent on ops page                |
| 18  | Pending → active sync           | ⚠️     | No push on password-set; poller off by default → onboarded users read "Suspended"     |
| 19  | RolesEditor — create role       | ⚠️     | No delete, no rename, no description field                                            |
| 20  | RolesEditor — permissions       | ⚠️     | Works; **`auth.role_permissions` is unaudited**                                       |
| 21  | Model tenant                    | ⚠️     | Mirror-only as designed; ships with zero roles; no backfill                           |
| 22  | Operations staff                | ⚠️     | Sync likely 409s (`push_roles_to_workos` bypasses the core-key fallback); no resend   |
| 23  | Operations roles                | ✅     | Works; `system` toggle has no extra guard                                             |
| 24  | Permission registry sync        | ✅     | Idempotent, single source of truth, TS generated                                      |
| 25  | Reconcile roles from WorkOS     | ❌     | Endpoint exists; **no UI calls it**                                                   |
| 26  | Global users list               | ✅     | Real; no firm column, no pagination                                                   |
| 27  | User detail — role change       | 💥     | **Posts to a non-existent endpoint (404) with a hardcoded role list**                 |
| 28  | User detail — memberships       | 💥     | Duplicate `each` key crashes for dual-portal users; `portal` not modelled             |
| 29  | User detail — deactivate        | ⚠️     | Works, but labelled "membership" while suspending the whole person                    |
| 30  | Audit log                       | ⚠️     | 100-row cap, server filters unused, no export, no `detail`, no reads/auth events      |
| 31  | WorkOS events poller            | ⚠️     | Off by default; no health surface; poison event blocks the cursor                     |
| 32  | Internal endpoints              | ✅     | Constant-time token, 404-on-miss, plaintext keys over the internal wire               |
| 33  | Dashboard                       | ✅     | Entirely real data with permission-aware fallback routing                             |
| 34  | SearchBar                       | 🎭     | Five hardcoded fictional firms, all linking to `/tenants`                             |

## Top five severest gaps

1. **The "Enabled" toggle blocks nothing (🎭, security).** `auth.tenants.enabled` is written by `PATCH /admin/tenants/{id}` and read by _no_ authentication or authorisation path — `auth.resolve_claims` (`0001_auth_core.sql:503`) ignores it, no RLS policy references it, and the one endpoint that returns it (`GET /me/tenant`) is called by no portal. Meanwhile `settings/+page.svelte:117` promises "Every member of {firm} will be blocked from signing in". Combined with the absence of tenant deletion (#2 below), Graphene currently has **no way to cut off a firm** — the disable switch is the only control offered and it is inert. `auth.users.enabled` is equally unenforced, so the whole-person "Suspend" relies solely on the WorkOS membership call succeeding.

2. **No tenant offboarding path at all (❌).** No delete, no archive, no soft-delete column, no UI. A firm that leaves the platform stays live indefinitely with working logins. When it is eventually built, `audit.log.tenant_id ON DELETE CASCADE` (`0001_auth_core.sql:172`) means a naive `DELETE` destroys that firm's entire compliance trail — the retention question needs answering before the endpoint exists.

3. **Role permission changes leave no audit trail (⚠️, compliance).** `audit.attach` covers 35 tables but not `auth.role_permissions` (`0001_auth_core.sql:799`). `set_org_role_permissions` deletes and re-inserts grants (`rbac.py:76-92`), so granting a role `contacts:read_all` — the documented "see all clients vs own book" lever — is completely invisible in `audit.log`. Nothing else records it either: WorkOS-side writes are unaudited, and no auth or read events are captured anywhere. For an FCA-facing product this is the highest-value missing evidence in the system.

4. **The user-detail role change has never worked, and its role list is hardcoded (💥).** `users/[id]/+page.server.ts:59` `PATCH`es `/admin/tenants/{id}/members/{user}` — a route that does not exist (the real one is `…/portals/{portal}`), so every submission 404s. Both the server validation (`:36`) and the dropdown (`+page.svelte:26`) use the stale five-slug array, which cannot match a per-tenant role set. The same page throws a duplicate-key error for any person holding two portals in one firm, because it keys the membership `each` on `tenant_id` while the API returns one row per portal. Three independent defects on one screen.

5. **Onboarding completion never propagates, so the mirror lies (⚠️).** Setting a password verifies the email in WorkOS but writes nothing locally (`set-password/+page.server.ts:21`), and the only reconciler — the events poller — is disabled by default in both `config.py:116` and `docker-compose.yaml:55`. A person who has fully onboarded therefore keeps `enabled=false`/`status='pending'` and renders as a red **"Suspended"** badge (`members/+page.svelte:214`) until an operator clicks "Sync from WorkOS" on that specific tenant. When email delivery is the failure instead, the resend link the API mints is discarded by the UI (`members/+page.svelte:36-39`) and absent entirely from the ops-staff page — leaving no recovery path. There is no health indicator anywhere for the poller, so this drift is silent.
