# Auth audit — every portal, every path, every fallback (19 Aug 2026)

**STATUS: the §8 rip-out was EXECUTED on 19 Aug 2026 (migrations 0107/0108 — app-only
classification, one firm org, every fallback deleted). This document remains as the audit that
drove it; `docs/auth.md` describes the current model.**

A full trace of how logins are handled, how portal access is granted and revoked, and every
fallback/legacy branch still live — on `main` @ `47a4c5f3`. Four independent code traces (frontend
flows, API classification, grant/revoke lifecycle, fallback inventory), synthesised here. Every claim
is file:line-cited from the actual code.

**Companion docs:** [`auth.md`](auth.md) (needs an update pass after the rip-out),
[`auth-applications-migration.md`](auth-applications-migration.md) (the portal-via-application design).

---

## 0. The headline

**The portal-via-application model is built, but it cannot activate anywhere except by hand.**
The app path only switches on when a portal application's _secret_ is stored
(`environments.py:616-622`), and **nothing on `main` can write those secrets** — the only setter,
`set_environment_app_secrets` (`environments.py:504-538`), has zero callers (the one-off migration
script that called it was deleted after use because it contained pasted secrets). Likewise
`operations_host` (0102) has **no writer at all**.

Consequence: outside the hand-patched dev DB, **every session in every deployment is classified by
the org fallback** — the exact legacy path the team wants gone — and the fallback's unmatched-org
default is `'platform'` (staff). That is the root of the deployed wrong-portal bounce (§6), and it is
why the rip-out has to be preceded by a small amount of _building_ (§8, step 1).

---

## 1. How a login works — client & platform portals

The two portals are byte-identical in this surface except cookie names and the permissions fetch.

1. **Host → environment.** Every request resolves the host via `resolveEnvironment`
   (`pkgs/auth/src/environment.ts:95-128`): `GET /internal/env-config?host=…` (60s cache, negative
   cache on 404). The API answers from `env_config_by_host` (`apps/api/app/environments.py:541-650`):
   the host's portal **application client id** (only when its secret is stored), the matching **app
   secret** (else the environment key), the **org to sign into** —
   `COALESCE(firm_org_id, per-portal org)` — cookie password, branding.
2. **Which credentials sign in** (`apps/*/src/lib/server/workos.ts:44-84`):
   `appClientId ?? workosClientId` paired with the served key; if the host is unknown / any of the
   three fields missing → the **`$env` single-environment fallback** (`WORKOS_CLIENT_ID` /
   `WORKOS_API_KEY` / `WORKOS_COOKIE_PASSWORD` / `WORKOS_ORGANIZATION_ID`). Nothing pins the resolved
   application to the portal that is running — the host row is the only binding.
3. **The login action** (`routes/login/+page.server.ts:56-133`): rate-limit (fails open —
   `environment.ts:219-248`), open-redirect-safe `?next`, `passwordAuth`
   (`pkgs/auth/src/auth.ts:95-169`) with branches: `misconfigured` (cookie password <32 chars → 503),
   `failed` (401 + `login_failed` auth event), `mfa`/`enroll` (pending cookies → `/mfa`,
   `/mfa/enroll`), `verify` (self-hosted set-password email), success → sealed session cookie
   (`wos-session-client|platform`, httpOnly, lax, secure, ~400-day cookie life) + `login` auth event.
   **Multi-org accounts**: `authenticateWithOrganizationSelection` second leg (`auth.ts:138-164`) —
   if no `organizationId` is known the user is told "Invalid email or password" though the password
   was right.
4. **Every request** (`hooks.server.ts`): sealed session authenticate + refresh (proactive at <60s
   to expiry, `pkgs/auth/src/session.ts:75-109`), then locals. **Platform fetches `permissions` from
   the API per request** (`fetchPermissions`, fails closed to `[]`); **the client portal fetches no
   permissions at all** — client access is RLS-scoped, not permission-gated (by design).
5. **Wrong-portal refusal**: a **confirmed** mismatch on `GET /me`'s portal deletes the local
   cookie and bounces to `/login?wrong_portal=1`; any error fails open. **Client portal (Sep 2026):**
   decided in `hooks.server.ts` for every request — `+server.ts` endpoints and form actions get a
   403 `{ error: 'wrong_portal' }` — and the WorkOS session is **revoked** (`revokeSession` with
   the authenticate result's `sessionId`) before the cookie goes (`@graphene/auth` `gates.ts`).
   **Platform/admin:** still root-`+layout.server.ts` only, cookie deleted, nothing revoked, so a
   wrong-portal session can still POST until a page load happens (API RLS remains the real gate).

### Admin portal differences

- Resolves the **operations org** via `GET /internal/operations-org`, cached for the process
  lifetime (`apps/admin/src/lib/server/workos.ts:22-38`); `cfg.organizationId ?? operationsOrgId`.
- **No** `WORKOS_ORGANIZATION_ID` fallback, **no** dev-auth bypass, **no** wrong-portal refusal, and
  its login `notice()` has no `wrong_portal` branch.

---

## 2. How the API authenticates a request

`get_current_principal` (`apps/api/app/auth.py:250-388`):

1. Reads `org_id`/`iss` **unverified** only to select the environment (`tenant_registry` —
   `_by_org` is built from the **three per-portal org columns**, _not_ `firm_org_id`:
   `tenant_registry.py:97`). Registry miss → the configured fallback env. The per-environment JWKS
   signature (RS256, `exp`/`sub` required, 60s leeway) is the real gate.
2. **Permissions come from the DB, never the token** (`_permissions_for`, `auth.py:120-153` →
   `auth.member_permissions`, 0093). WorkOS's embedded role-permissions blew the ~4KB cookie ceiling;
   they are never pushed to WorkOS any more (`rbac.py:1-14`). A role/permission change takes effect
   on the **very next request** — no re-login. The one surviving token-permissions read is the
   dev-bypass (`auth.py:311`).
3. `resolve_claims_cached` memoises the SECURITY DEFINER resolve per request, keyed
   `(user, org, client_id)`.
4. **Break-glass** `SYSTEM_USER_IDS` grants the `system` permission on every request, any stage,
   unaudited beyond a WARNING (`auth.py:158-186`).
5. **Dev `dev:` bypass** — quad-guarded (flag + dev stage + loopback + unproxied), off by default.

### Portal classification (`auth.resolve_claims`, live body = 0101)

- **App branch** (`0101:68-80`): token's `client_id` matched against the tenant's `*_client_id`
  columns across any env row → proposes the portal; a staff/ops proposal is **membership-gated**
  (`0101:99-108`): no active `organisation_memberships.portal` row → downgraded to `client`.
- **Org fallback** (`0101:85-95`): fires whenever the app branch misses — which is **every
  production session today** (§0). It is **ungated**, and its unmatched default is
  **`ELSE 'platform'`** — the widest branch in the file. A `firm_org_id`-authenticated session
  matches neither per-portal org column and lands there.
- The `role='client'` belt survives in `auth.tenant_lineage()` (`0030:126`).
- Four call sites still call the 2-arg form (no `client_id`): `actions.py:844`, `actions.py:1113`,
  `aml.py:111`, `storage.py:74` — attribution-only, but they always take the org path.

---

## 3. How access is granted

One primitive: **`grant_portal_access`** (`provisioning.py:276-516`), reached from admin
(`PUT /admin/tenants/{id}/members/{uid}/portals/{portal}`) and firm self-service
(`PUT /firm/members/{uid}/portals/{portal}`, `members:write`; client invites via
`POST /firm/contacts/{id}/portal-invite`).

1. Role resolved by `(tenant, slug)`; role↔portal mismatch refused (`:321-325`) — the only place
   that axis is enforced on a membership write.
2. WorkOS: find-or-create the user (no password — self-hosted onboarding), membership into
   **`COALESCE(firm_org_id, per-portal org)`** with the `org-`-prefixed role; conflict → update role
   - reactivate.
3. Unverified email → mint a `reset_password` token → branded Resend email to our own
   `/set-password?token=…` → **sweep the user's live sessions** (every mint does).
   WorkOS exposes **no revoke** for these tokens — a superseded link lives until expiry.
4. Mirror: `auth.users` upsert (unique per `(tenant, email)`), `auth.organisation_memberships`
   upsert keyed `(user, tenant, portal)` with `status = pending|active`. **`memberships.portal` is
   the real access control** — it feeds `role_ids` → permissions, and the app-path membership gate.
5. Onboarding completes via `POST /internal/onboarding-complete` (verifies `email_verified` with
   WorkOS first; only promotes `pending` → `active`, so it can't un-suspend anyone).
6. The events poller (60s) + `sync_tenant` reconcile the mirror from WorkOS —
   **but both still read only the per-portal org columns** (§7, H2).

## 4. How access is revoked

| Lever                                           | What happens                                                                                                                                                                                | Where                                                             |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| **Per-portal revoke**                           | WorkOS membership → `inactive`; mirror row → `inactive`; **all** the person's sessions swept                                                                                                | `provisioning.py:603-634`                                         |
| **Whole-person suspend**                        | every portal deactivated, sessions swept, mirror `inactive`, `users.enabled=false`; snapshot audited for exact reactivation                                                                 | `provisioning.py:1065-1212`                                       |
| **Firm kill switch**                            | `tenants.enabled=false` → every member's sessions revoked; every request 403s at `_refuse_if_suspended` (fails closed; deliberately not on admin_session); forgot-password checks it itself | `provisioning.py:819-905`, `db.py:64-101`, `internal.py:189-194`  |
| **Firm delete**                                 | requires suspended + typed alias; refuses ops/model/own tenant; deactivates + sweeps everyone; export artefact                                                                              | `provisioning.py:1697-1814`                                       |
| **MFA reset / invite resend / password change** | all sweep sessions                                                                                                                                                                          | `provisioning.py:1480`, `:741`; `pkgs/auth/src/sessions.ts:59-97` |

**Effect timing:** permissions are DB-read per request, so revocation of a staff role bites on the
next API call. A **client** login has no permissions to lose — revocation relies on the WorkOS
membership deactivation (blocks new sign-ins) + the session sweep; residual window = the live access
token's remaining life + 60s leeway. `auth.users.enabled` is read **nowhere** in the request path.

**Sweep gaps (real):** completing `/set-password` does **not** sweep other sessions
(`internal.py:308-385`), and `/internal/forgot-password` mints without sweeping — only
admin/firm-initiated mints sweep.

---

## 5. The deployed wrong-portal incident, explained

Chain (each step cited above): the client portal resolves its host → `env_config` serves
`organization_id = firm_org_id` (the COALESCE) but **no app client id** (no app secret stored on
that path) → the user signs into the **firm org** with the **environment** client id → the token's
`client_id` matches no `*_client_id` column → org fallback → the firm org matches neither
`client_portal_org_id` nor `operations_org_id` → **`ELSE 'platform'`** → `/me/portal` says
`platform` → the client shell's refusal fires: _"That account does not have access to this portal."_

The same chain (via the `$env` fallback instead) applies when the deployed host isn't registered at
all. Either way: **half-migrated state + ungated org fallback = wrong classification.** The fix is
not another patch on the fallback — it is completing the app path and deleting the fallback (§8).

---

## 6. Live correctness hazards found (beyond the incident)

Ranked. H1–H4 are active bugs under the one-firm-org transition, today.

- **H1 — org fallback defaults to staff.** Unmatched org ⇒ `'platform'` (`0101:93`), ungated. Any
  firm-org session that reaches it is classified staff. (Data safety holds only because a platform
  classification with no platform membership yields zero `role_ids` ⇒ zero permissions, and RLS
  contact scope stays own-links — but the client is bounced from their own portal, and the posture
  is wrong.)
- **H2 — the reconciler misattributes client members.** `sync_tenant` (`provisioning.py:1276-1291`)
  and the events poller's `_portal_for_org` (`events.py:222-246`) read only the per-portal org
  columns; a membership event for the **firm org** defaults to `'platform'` — a client grant can
  reconcile into a `portal='platform'` mirror row.
- **H3 — `set_member_role` has no portal check** (`provisioning.py:1018-1041`): a platform role can
  be attached to a `portal='client'` membership; `resolve_claims` filters `role_ids` on the
  _membership's_ portal, not the role's, so a client session could carry staff permissions. The
  role-editor side refuses this axis (`rbac.py:192-203`); the membership side doesn't. An unknown
  slug silently sets `role_id = NULL`.
- **H4 — one firm org ⇒ one WorkOS membership for client+platform.** `_workos_membership`
  (`provisioning.py:993-1015`) matches by org id, so revoking the client portal deactivates the
  WorkOS membership that also carries platform access, and a client grant overwrites the WorkOS
  role of a platform holder.
- **H5 — wrong-portal refusal deletes the cookie but revokes nothing**; refusal is layout-load-only. _Closed on the client portal (hook-level gate + `revokeSession`); open on platform/admin._
- **H6 — `tenant_registry._by_org` doesn't know `firm_org_id`** (`tenant_registry.py:97`): firm-org
  tokens resolve their environment by issuer or fall to the configured env.
- **H7 — sweep gaps** (§4) and the break-glass posture (production, unaudited, non-expiring).
- **H8 — `tenant_enabled` is published to the portals and dropped** (`environment.ts:75-88`): a
  suspended firm's user completes the portal login and only fails at the API.

---

## 7. Complete fallback / legacy inventory

The full itemised inventory (34 API items + 27 frontend items, each with activation condition,
break-risk and deletion precondition) lives in the audit traces; the classes, deduplicated:

| #   | Fallback / legacy                                                                                                 | Where                                                          | Status                                          |
| --- | ----------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- | ----------------------------------------------- |
| 1   | Org-based portal classification + `ELSE 'platform'`                                                               | `0101:85-95,109-111`                                           | **The live path everywhere. Delete last.**      |
| 2   | `role='client'` belt in `tenant_lineage`                                                                          | `0030:126`                                                     | Delete with/after #1                            |
| 3   | Per-portal org columns + `ensure_portal_orgs` + every reader                                                      | `0001:188-195`; readers in registry/events/sync/admin UI/tests | Delete after #1 + repoints                      |
| 4   | `COALESCE(firm_org_id, …)` ×3                                                                                     | `environments.py:187, :345-350, :578-582`                      | Reduce to plain `firm_org_id` after backfill    |
| 5   | App id gated on app secret; env-key fallback                                                                      | `environments.py:612-629`                                      | Remove once secrets have a write path           |
| 6   | Frontend `appClientId ?? workosClientId`                                                                          | `workos.ts:57/:75` ×3                                          | Remove with #5                                  |
| 7   | `$env` single-environment fallback + `WORKOS_ORGANIZATION_ID`                                                     | `workos.ts:67-83` ×3                                           | Register localhost as a real host, then delete  |
| 8   | Admin `operationsOrgId` via `/internal/operations-org`                                                            | `admin/workos.ts:22-38`                                        | Delete once `operations_host` is registered     |
| 9   | `workos_organisation_id` on `auth.tenants` + readers                                                              | `0001:123-127`, `0101:52-55`, `tenant_id()` psql arm           | Drop after `users.tenant_id` NOT NULL check     |
| 10  | Registry-miss → configured-env verification                                                                       | `auth.py:331-337`                                              | Make a hard 401 once the core env is registered |
| 11  | Dev `dev:` bypass + `dev-auth` cookie                                                                             | `auth.py:278-315`, hooks ×2                                    | Needs a real E2E auth strategy first            |
| 12  | `SYSTEM_USER_IDS` break-glass                                                                                     | `auth.py:158-186`                                              | Needs an empty allowlist + real ops role        |
| 13  | `role_claim` namespacing; dead `role` param in `can()` (~325 sites); `AuthenticateResult.permissions`             | `auth.py:450-476`; generated `permissions.ts`                  | Mechanical cleanups                             |
| 14  | Dead: signup validators + 3 `validation.ts` shims; `PENDING_COOKIE` ×3; unnamespaced cookie names in `cookies.ts` | `pkgs/auth`, `workos.ts` ×3                                    | **Delete now, zero risk**                       |
| 15  | `verify_aud: False`; issuer unpinned on org-match; `aud` app-id fallback                                          | `auth.py:363, :330, :54-58`                                    | Pin at cutover                                  |
| 16  | 2-arg `resolve_claims` call sites                                                                                 | `actions.py:844,1113`, `aml.py:111`, `storage.py:74`           | Pass `client_id`                                |

---

## 8. The rip-out plan (ordered; each step independently safe)

**Step 0 — delete now, zero risk:** item 14; `AuthenticateResult.permissions`.

**Step 1 — BUILD the missing write paths** (the whole plan gates on this):

- `PUT /admin/environments/{id}/app-secrets` + admin UI field calling
  `set_environment_app_secrets`; add `operations_host` to `set_environment`/`RegisterEnvironment`
  and the admin form; extend the P6 checklist to cover both.
- Repoint `tenant_registry._by_org` to include `firm_org_id` (one line, independently safe).
- Fix H2/H3/H4: key `_workos_membership`/`sync_tenant`/`_portal_for_org` on
  `organisation_memberships.portal` (not org identity); add the role↔portal check + unknown-slug
  refusal to `set_member_role`.

**Step 2 — configure every live environment:** firm org backfilled, three app ids + three app
secrets stored, hosts (incl. operations) registered, then one confirmed login per portal whose
token carries the matching `client_id`.

**Step 3 — the cutover deletions, in order:** org classification branch (make no-app-match a hard
refusal, never `'platform'`) → `role='client'` belt → per-portal org columns + `ensure_portal_orgs`

- admin "Orgs" UI → the three COALESCEs → app-secret gate + frontend `??` fallbacks → `$env`
  fallback + `WORKOS_ORGANIZATION_ID` + admin ops-org endpoint → `workos_organisation_id` →
  registry-miss fallback → pin `iss`/`aud`. Migrate the RLS suite alongside and keep it green at each
  step.

**Step 4 — independent tracks:** dev-bypass replacement for E2E; break-glass retirement; sweep
gaps (set-password + forgot-password session sweeps); wire or delete `tenant_enabled` at sign-in;
revoke the WorkOS session on a wrong-portal refusal.

**End state:** one login path per portal (host → its application + its secret → the firm org), one
classifier (app id, membership-gated, hard-fail on no match), access = `organisation_memberships.portal`
rows, revocation = membership deactivation + session sweep, zero fallbacks.
