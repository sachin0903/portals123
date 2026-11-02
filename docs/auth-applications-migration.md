# Auth evolution: WorkOS applications for portals, and portal-scoped permission slugs

**Status: DONE — executed in full, including the rip-out (0093–0108; app-only classification, one firm org, zero fallbacks). Kept as the design record; `docs/auth.md` is current reality.** This is an impact study of two planned changes to the auth
model, requested Aug 2026. It complements [`auth.md`](auth.md) (current reality) and
[`identity-and-access.md`](identity-and-access.md) (the decision-level model). Nothing here is
implemented; the point is to map what each change touches _before_ committing to it.

The two changes:

- **Change A — portal = WorkOS _application_, not _organisation_.** Today a firm's project models each
  portal (Client Portal, Platform, Operations) as a WorkOS **organisation**, and the token's `org_id`
  says which portal the session is for. Move that boundary onto WorkOS **applications** (one per
  surface), each with its own client id, sign-in surface and session policy.
- **Change B — encode the portal in the permission slug.** Today a permission is `resource:action`
  (e.g. `contacts:read`) with `portal` as a separate _attribute_. Make the slug itself
  `portal:resource:action` (e.g. `platform:contacts:read`).

**Bottom line up front.** Change A is the larger and more _correct_ change — it stops overloading
"organisation" (WorkOS's multi-tenancy primitive) to mean "portal" (a product surface), which is
exactly what WorkOS applications are for. But it has **two feasibility unknowns that must be confirmed
against a live WorkOS environment before any code is written** (below), and because we are
project-per-tenant it is per-firm work × N environments. Change B is cheap in Python (the registry is
already the single source of truth) but **expensive in SQL and in live WorkOS state**, and its
practical benefit today is thin because the client portal currently uses **zero** permissions. I
recommend confirming A's unknowns first, doing A as an expand/contract migration, and folding B in
**only** alongside A (or deferring it) — details under [Sequencing](#sequencing--recommendation).

---

## How the portal boundary works today

The portal is an **organisation** inside the firm's WorkOS project. The moving parts:

| Artefact                                                       | Where                                                           | Role of "the org"                                                                                           |
| -------------------------------------------------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `client_portal_org_id`, `platform_org_id`, `operations_org_id` | `auth.tenant_environments` (`0001`)                             | The three portal orgs per environment.                                                                      |
| Portal orgs created / found                                    | `environments.py` `ensure_portal_orgs`, `ensure_operations_org` | One WorkOS org per portal, by name.                                                                         |
| Invite → membership                                            | `provisioning.py` (`create_organization_membership`)            | A user is invited _into the portal org_; per-portal access = org membership.                                |
| Roles                                                          | `environments.py` `push_roles_to_workos`                        | **Organisation roles** on the portal org (`org-` prefixed).                                                 |
| Which portal a host is                                         | `environments.py` `env_config_by_host`                          | Returns the portal's `org_id` so the sealed session signs into the right org (no `organization_selection`). |
| Token → env                                                    | `auth.py` + `tenant_registry.py`                                | `org_id`/`iss` select the environment; the org also _is_ the portal.                                        |
| Claims                                                         | `auth.resolve_claims` (`0001`)                                  | Prefers the **login's** `tenant_id`; `workos_organisation_id` is a transitional fallback.                   |
| The client DENY lever                                          | `auth.tenant_lineage()` (`0001`), `auth.role()='client'`        | A client-portal session gets an empty staff lineage. `role` comes from the org-role.                        |

The awkwardness the change targets: **"organisation" is doing two jobs** — it is WorkOS's tenancy
primitive, and we've bent it to mean "portal". That is why `org_id` denotes a portal rather than a
firm, why a person who is both a client and a staffer needs two org memberships, and why roles are
org-roles keyed to a portal.

---

## What WorkOS applications are (verified facts + unknowns)

Confirmed from the WorkOS docs (Aug 2026 — see [References](#references)):

- **Each application has its own `client_id`, redirect URIs, session configuration and credentials.**
  Applications model distinct _surfaces_ within one environment.
- **Users and organisations are shared across applications** in an environment — one user pool, one
  set of orgs, a unified login experience, no duplicate accounts.
- **Token claims:** the `iss` claim is the **same for all applications** in an environment (it
  references the _default_ application's client id). The **`client_id` claim reflects the current
  application** — this is how "which application/surface" is represented in the token.
- **JWKS is per environment** (served at `api.workos.com/sso/jwks/<clientId>`). WorkOS's own JWT guide
  stresses validating `iss`, `aud`, `exp`, `nbf` — not just the signature.

### What the OpenAPI spec settles (authoritative — [workos/openapi-spec](https://raw.githubusercontent.com/workos/openapi-spec/refs/heads/main/spec/open-api-spec.yaml), reviewed Aug 2026)

The two feasibility unknowns are now answered against the spec, and the answers reshape the design.

1. **AuthKit multi-surface "Applications" are NOT in the management API.** The spec has **no
   `Application` object or endpoint** for the per-surface feature. The only "applications" it models
   are WorkOS **Connect** (OAuth/M2M) — `/connect/applications`, schemas `ConnectApplication` /
   `CreateOAuthApplicationDto` — plus OAuth-**consent** tracking (`/organizations/{id}/authorized_applications`
   and `/user_management/users/{id}/authorized_applications` both read "Connect applications the
   user/org has authorized"). Redirect URIs list **per environment** (`/user_management/redirect_uris`);
   the JWT template is **singular per environment** (`/user_management/jwt_template`). **Conclusion:
   configuring a portal application is dashboard-only.** That is a _regression_ from today — portal
   **orgs** are API-created (`ensure_portal_orgs` → `organizations.create_organization`), so under
   project-per-tenant, moving to apps means hand-creating 2–3 applications per firm in the dashboard,
   which `environments.py` cannot automate. (Caveat: the feature is new and the public spec may lag; a
   private/beta endpoint may exist — worth one question to WorkOS — but plan for dashboard-only.)
2. **Per-application access is not gated by WorkOS — any account in the environment can sign into any
   application** (confirmed by the team's WorkOS knowledge; consistent with (1)). This is the sharpest
   consequence of the whole change: today a client is kept out of the Platform surface by _org
   membership_; with a shared user pool and no app gating, nothing at WorkOS stops a client obtaining
   a token for the Platform app. **So the gate must be ours, and taking the portal from the app alone
   is unsafe.** The rule (built in `0094`, and strictly safer whatever WorkOS does, so it is
   unconditional): **the app PROPOSES a portal; our `auth.organisation_memberships` table DISPOSES** —
   a staff/ops portal is granted only when the user holds an active membership for it, else it
   downgrades to `client` (own-scope only), so the app path can never hand a non-member a staff
   lineage. The sign-in path additionally refuses such a session for UX (P4/P5); `resolve_claims` +
   RLS is the hard data-safety gate behind it.
3. **Roles and permissions do NOT sit on the portal boundary — good news.** The spec confirms
   **permissions are environment-level** (`/authorization/permissions` — "all permissions in your
   WorkOS environment") and **roles are environment-level _or_ organisation-level**
   (`/authorization/roles`, `/authorization/organizations/{organizationId}/roles`). Applications share
   the environment, so **moving the portal from org to application requires no re-homing of roles or
   permissions in WorkOS.** Today's code ties roles to the _portal org_ purely by choice
   (`create_organization_role` on the portal org); that anchor moves to the firm org (or to
   environment roles), and nothing about the roles/permissions _API_ changes.

---

## The fork the findings expose: A1 vs A2

Because applications aren't API-provisionable but organisations are, "portal = application" forces a
choice about **project-per-tenant** (the July 2026 white-label isolation decision,
[`identity-and-access.md`](identity-and-access.md) §0). There are two coherent targets:

**A1 — keep project-per-tenant; put the portal apps _inside each firm's environment_.**
Each firm stays its own WorkOS environment; within it, the two/three portal orgs become two/three
portal **applications**. Preserves hard per-firm isolation, per-firm JWKS, and per-firm white-label
sign-in. **Cost:** the portal apps are dashboard-only, so onboarding a firm now includes hand-creating
2–3 applications per environment — a real ops regression from today's fully-API-driven org creation.
Scales as _N firms × 2–3 manual app setups_.

**A2 — the canonical WorkOS B2B shape: few environments, org-per-firm, apps-per-portal shared.**
Collapse to one environment per _stage_; the **firm becomes an organisation** (API-created, exactly
what `ensure_portal_orgs`/`organizations.create_organization` already do — so firm onboarding stays
automated); the **portals become 2–3 applications created once** for the whole platform. This is the
model WorkOS applications were designed for and it removes the per-firm manual step entirely.
**Cost:** it _reverses_ project-per-tenant — firms would share one environment's user pool and JWKS,
which changes the isolation story and the identity model (`auth.users` is currently unique on
`(tenant_id, email)`; a shared pool per environment means WorkOS user-uniqueness is per environment,
so "the same person at two firms is two logins" needs re-examination), and it loses per-firm
white-label AuthKit sign-in unless applications provide enough per-app branding.

**This is the decision to make first — it changes everything downstream.** A1 is the smaller diff and
keeps the isolation guarantee at an ops cost; A2 is the larger diff and the "correct" WorkOS shape but
unwinds a deliberate prior decision. The component impact below is written for **A1** (the smaller,
isolation-preserving path); under A2 the same seams move, but firm-provisioning gets _simpler_ (orgs,
not apps) while the identity/isolation model gets _harder_.

### First firm set up (Graphene core tenant, Aug 2026)

The three portal applications for the **Graphene** tenant, created in the WorkOS dashboard (these are
public client ids, not secrets — used to classify the `portal` claim in P2/P3, and to seed the
`*_client_id` columns from 0093):

| Portal              | Application client id               |
| ------------------- | ----------------------------------- |
| Client Portal       | `client_01M04WJZ4YKEGVCYN1NCB48XM0` |
| Platform            | `client_01M04WKJG8H4G8C4VWSPX3BFKB` |
| Internal Operations | `client_01M04WM8ANMG4RKFWH7989D5XX` |

Confirmed from these: each application has a **distinct client id**, and all three share **one signing
key** (`kid sso_oidc_key_pair_01KWF14QTSK0TFVM3TWWC7TYN9` via `api.workos.com/sso/jwks/<client_id>`) —
so JWKS verification is unchanged and the portal is identified by the token's `client_id`/`aud` claim.
**Still to confirm from a real login token:** which claim (`client_id` vs `aud`) carries the app id in
the _access_ token, and the `iss` value. That gates P2/P3.

## Build plan (A1 — CHOSEN)

Decided: **A1** — keep project-per-tenant; portal apps live inside each firm's environment; the manual
per-firm WorkOS app setup is tracked by an **admin onboarding checklist** (below). **Change B
(portal-prefixed slugs) is deferred** — not needed for A1, and its cost is RLS-literal churn.

Expand/contract, so nothing breaks mid-flight. Each phase is shippable on its own.

- [x] **P1 — Schema (expand, no behaviour change).** DONE — migration `0093_portal_applications`
      adds `auth.tenant_environments.{client_portal,platform,operations}_client_id` alongside the
      `*_org_id` columns. Additive, nullable; chain validated.
- [x] **P2 — Principal carries the app client id.** DONE — `app/auth.py` `Principal.client_id`
      (from the token's `client_id`/`aud` claim, `_app_client_id`), passed into `resolve_claims` at all
      five `db.py` session dependencies. _(Deferred to cutover: enforcing `aud` — kept off so we never
      reject a token on an unconfirmed claim.)_
- [x] **P3 — Portal claim classified from the app.** DONE — migration `0094_portal_from_application`
      gives `resolve_claims` a third `p_client_id` arg and classifies the portal from it, **falling back
      to the org-based branch**. Behaviour-preserving until a firm's `*_client_id` columns are populated
      (they're all NULL today). Green: ruff + mypy + `test_auth.py` (29). **Pending: `test_rls*.py`
      against the live stack.** _(This corrects the earlier assumption — the portal-claim machinery in
      `0004_portal_scope` already existed; this only swaps the derivation input.)_
- [~] **P4 — Provisioning + host resolution.** BACKEND PLUMBING DONE: `environments.set_environment_applications`
  stores the per-app client ids (COALESCE-preserve), and `env_config_by_host` now returns
  `app_client_id` for the host's portal (NULL → falls back to the env client id). Green: ruff + mypy.
  Portal access is enforced by OUR `organisation_memberships` (WorkOS doesn't gate app access —
  confirmed), and the **`resolve_claims` membership gate is built in `0094`** (app proposes, membership
  disposes; a non-member never gets staff reach). Host uniqueness DONE (`0095`, closes review #1).
  STILL TO DO — and these two need the **org-model decision** (below): a **sign-in refusal** at the
  portal (UX in front of the RLS gate), and `push_roles_to_workos` roles → firm org (or env roles).

**Verification (Aug 2026): migrations 0093–0095 + `resolve_claims` proven against real Postgres** —
`test_db.py` + all `test_rls*.py` + `test_book_session.py` = 159 tests green on ephemeral testcontainers
(the migrations apply, the app path + membership gate work, the org path is unchanged). NOT yet applied
to the live DB — the API here points at a remote/shared Postgres unreachable from the dev sandbox, so
`moon run api:db-migrate` must be run in an environment that can reach it (and deliberately, since it's
shared).

**The org-model decision (needed for the last two P4 items and for P7).** Under app-per-portal, WorkOS
orgs are no longer portals. Option (b) as first framed — "drop orgs entirely, roles at the environment
level" — was investigated against the spec and is **NOT possible**: WorkOS assigns roles ONLY through
an organisation membership (`/authorization/organization_memberships/{id}/role_assignments`), a group,
or a resource — there is no user-level, org-less role assignment, and `/authorization/roles` are role
_definitions_, not assignments. Since this platform carries permissions in the token (role → membership),
permission-bearing staff must belong to an org.

**So the viable clean model is: ONE firm org per environment** (not three portal orgs). A user is a
member of the firm org once, with a role; the **portal comes from the app** (`client_id`) and
**portal access is our `organisation_memberships.portal`** (WorkOS doesn't gate it). Role
_definitions_ can still live at the environment level and be assigned via that single firm org. This
removes the per-portal-org overloading (the legacy the team wants gone) while staying WorkOS-legal —
effectively (a) plus environment-level role definitions. This is the chosen direction.

- [x] **P5 — Frontend.** DONE — `@graphene/auth` `EnvironmentConfig.appClientId` (mapped from
      `app_client_id`), and all three portals' `resolveAuth` sign in through `cfg.appClientId ?? cfg.workosClientId`
      (fallback = behaviour-preserving). Green: `auth:lint`/`auth:test` (196) + `client/platform/admin:check`
      (0 errors). Admin: `env_config` serves no ops-host app id yet, so it falls back (unchanged).
- [x] **P6 — Admin onboarding checklist.** DONE — the tenant WorkOS page (`environments`) now has a
      **Portal applications** card: a checklist that auto-ticks off real stored state (project registered ·
      hosts set · keys set · Client/Platform apps created) and a form to record each portal's app client id
      (`PUT /admin/tenants/{id}/environment/applications` → `set_environment_applications`, `tenants:write`,
      COALESCE-preserve). Green: ruff + mypy + `admin:check`/`lint`/`test` (298). STILL TO DO here: the
      pre-existing gap — the **unique constraint on portal hosts** (own small migration).
- [ ] **P7 — Contract: DELETE the legacy org-per-portal path (mandatory — no dual system left).**
      The dual-read in P3 is transitional scaffolding, not a second system to keep. Once every live firm
      signs in through its apps and one real login has confirmed the token claim, remove ALL of it:
  - the org-based branch in `auth.resolve_claims` (leaving app-only classification) and the
    `workos_organisation_id` fallback;
  - the `client_portal_org_id` / `platform_org_id` / `operations_org_id` columns (migration, drop);
  - the `auth.role() = 'client'` legacy "belt" in `auth.tenant_lineage()` (the app-derived `portal`
    claim is now the sole client-deny signal);
  - `ensure_portal_orgs` / org-membership-as-portal in `provisioning.py`, replaced by app grants;
  - the org-based branch of `env_config_by_host`.
    End state: application-only, one implementation, nothing legacy.
    **GATE (do not skip):** P7 removes the org fallback, which is the safety net covering the ONE
    unverified assumption — that a real WorkOS access token from an application actually carries the
    `client_id` claim (documented by WorkOS, read with an `aud` fallback, but not yet observed on a live
    token here). Deleting the fallback before that is confirmed turns a wrong assumption from "no-op,
    falls back to org" into "portal classification broken for everyone". So: apply 0093–0095 to the DB →
    save Graphene's app ids on the admin page → log in once → confirm a normal session → THEN P7. A
    migration rollback does not undo an exposure/lockout window on a shared or live DB, so "we can
    rollback" is not sufficient cover for skipping this order.

**Do first (both are prerequisites and cheap):** confirm with WorkOS whether a non-public API for
AuthKit applications exists (would let P6 automate creation), and stand up one firm's apps in the
dashboard by hand to nail down the exact steps P6's checklist encodes.

## The live cutover runbook (the remaining work is supervised, not headless)

Everything up to here (P1–P6 + host uniqueness) is code that lands and is verified on testcontainers.
The rest — the one-firm-org provisioning rewrite and P7 — makes **live WorkOS calls** and removes the
safety fallback, so it is a supervised cutover done against a real environment, in this order. Per
firm (start with Graphene):

1. **Apply migrations** `0093`–`0095` to the real DB (`moon run api:db-migrate` where `10.30.60.15`
   is reachable). Behaviour-preserving — nothing changes yet.
2. **Create the single firm org** in WorkOS (or designate the existing Platform org as the firm org),
   and record it (a `firm_org_id` on `auth.tenant_environments` — additive migration, part of the
   provisioning rewrite).
3. **Save the app client ids** on the admin tenant page (P6 UI is built) — for Graphene those are the
   three IDs already recorded in this doc.
4. **One real login** through a portal. Confirm two things from that session: it lands normally, and
   the access token carries `client_id` (a one-off log in `get_current_principal` or an inspected
   token). **This is the gate for everything below.**
5. **Provisioning rewrite goes live**: `ensure_portal_orgs`→`ensure_firm_org`; `grant_portal_access`
   joins the firm org once (portal access recorded in `organisation_memberships.portal`);
   `push_roles_to_workos` targets the firm org. Migrate existing memberships from the portal orgs to
   the firm org.
6. **Sign-in refusal**: the portal refuses a session whose membership doesn't include that portal
   (UX in front of the RLS membership gate, which already denies the data).
7. **P7 — delete legacy**: only now remove the org-based portal classification, the `*_org_id`
   columns, the `role='client'` belt, and the org-per-portal provisioning. Verified by migrating the
   RLS suite to the app path and re-running it green.

Steps 2–6 are per-firm and, given the 6-month onboarding, are done as part of that (tracked by the
P6 checklist). Because a firm on one-org with app ids unset would classify every session as staff via
the ungated org fallback, **one-org + app-ids + (eventually) P7 must land together for a given firm** —
they are not independently safe increments, which is why this is a runbook and not more merged code.

## Change A — impact, component by component

Assuming A1 (portal apps inside each firm's environment), here is what moves. Ordered by blast radius.

### 1. Token verification (`app/auth.py`) — moderate, well-contained

Today: read `org_id`/`iss` unverified → pick environment → verify against that env's JWKS → extract
`org_id` as the portal.
After: the environment is still selected by `iss` (now _stable across apps_ — the default app's
client id) or by `client_id`; the JWKS signature check is **unchanged** (still per-environment). Two
concrete edits:

- **Select the environment by `iss`/`client_id`, not `org_id`.** `org_id` no longer denotes a portal.
  `tenant_registry` gains an `application client_id → (environment, portal)` map; the current
  `_by_org` map's meaning changes (org now denotes the firm, if kept at all).
- **NEW: validate `aud`/`client_id` names the expected application**, and derive `portal` from it. The
  API currently sets `verify_aud: False`; with applications, the audience is the discriminator between
  portals and should be checked. This is the new gate that replaces "the org is the portal".

### 2. The environment registry & schema (`tenant_registry.py`, `auth.tenant_environments`) — schema migration

- Replace the three `*_org_id` columns with per-application ids/client-ids
  (`client_portal_app_id`/`platform_app_id`/`operations_app_id`, plus each app's `client_id` if it
  differs from the environment's). Hosts stay.
- `tenant_registry._build` maps **application client_id → env + portal** in addition to `iss → env`.

### 3. `env_config_by_host` (`environments.py`) — behaviour change

Today it returns the portal's `organization_id` so the sealed session lands in the right org. After,
the host resolves to an **application** (its `client_id` + AuthKit config); the SvelteKit WorkOS
client is constructed **per application** (per-portal `client_id`). The `organization_id` it returns
becomes the **firm** org (if we keep one) or is dropped. This also unlocks **per-portal session
policy** (e.g. a short platform-staff session, a longer client session) and **per-portal AuthKit
branding/redirects** — a real product win, not just tidiness.

### 4. Provisioning & roles (`provisioning.py`, `environments.py`) — the biggest rework

- **Membership** changes from "into the portal org" to "into the firm org (once), plus a grant of
  which _applications_ the user may use." The exact mechanism depends on unknown #2 above.
- **`push_roles_to_workos`** stops creating organisation roles per portal org. Roles re-attach to
  whatever the answer to the roles/permissions unknown is (firm org, or application). The `portal`
  attribute on `auth.roles` stays — it now drives which _application_ a role governs.
- `ensure_portal_orgs` / `ensure_operations_org` become `ensure_portal_applications` /
  `ensure_operations_application` — **and can only be automated if unknown #1 says applications are
  API-creatable.** If not, these become "record the dashboard-created application ids" functions.

### 5. The RLS client DENY lever (`0001` `auth.tenant_lineage`) — auth SQL migration

`auth.role() = 'client'` is how a client-portal session gets an empty staff lineage. With applications,
"which portal" is the token's `client_id`/`aud`, not an org-role. So:

- `build_jwt_claims`/`resolve_claims` must publish a **`portal` claim** derived from the token's
  application (`client_id`/`aud` → portal), and
- `auth.tenant_lineage()` must key the DENY on that `portal` claim instead of `auth.role()`.
  This is a small, surgical SQL migration but it is **security-critical** — get it wrong and a client
  session could gain a staff lineage. Pin it with the existing `test_rls*.py` battery.

### 6. `auth.resolve_claims` (`0001`, replaced in `0002`) — minor

Already prefers the login's `tenant_id`; the `workos_organisation_id` fallback becomes the **firm**
org (if kept) or is removed. Project-per-tenant already gives the tenant from the login, so this is
tidy-up, not load-bearing.

### 7. Frontend (`hooks.server.ts`, `lib/server/workos.ts`, `resolveAuth`) — small

Construct the WorkOS client per **application** (client id per portal); cookie names are already
per-portal. The sealed-session dance is unchanged.

### 8. Migration shape

Expand/contract, per environment:

1. Create the portal **applications** in each WorkOS environment (API if possible, else dashboard) and
   record their ids/client-ids alongside the existing `*_org_id` columns.
2. Teach verification to accept **either** signal (org-as-portal _or_ app-as-portal) — dual-read.
3. Move sign-in to the per-application client ids; publish the `portal` claim from the application.
4. Cut the RLS DENY lever over to the `portal` claim.
5. Drop the `*_org_id` portal columns and the org-per-portal provisioning.

---

## Change B — portal-prefixed permission slugs

Today (`app/permissions.py`): slug is `resource:action`; `portal` (`client`/`platform`/`operations`)
is a **column** on `Permission` and `auth.permissions`. The portal is _implicit_ in the slug
(`contacts:*` is platform, `aml:*` is operations, `book:*` is operations). Proposed: make it explicit —
`platform:contacts:read`, `operations:aml:adjudicate`, etc.

### What it would touch

| Area                                    | Impact                                                                                                                                                                                                                                                                                                                                                                |
| --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/permissions.py`                    | Cheap: `_resource`/`_reg` prepend `portal:`. `system` stays the special global slug.                                                                                                                                                                                                                                                                                  |
| `satisfies()` / TS `can` (write ⇒ read) | Parses the trailing `:read`/`:write` — still the last segment, so the logic holds; just re-verify the string ops.                                                                                                                                                                                                                                                     |
| **266 `require_permission` call sites** | **No change** — they pass `Permission` _constants_, not raw slugs. This is the payoff of the registry being the single source of truth.                                                                                                                                                                                                                               |
| **RLS policies & helpers (SQL)**        | **Expensive.** Policies reference **literal** slugs — `auth.has_permission('contacts:read_all')`, `auth.can_read_all()`, `'system'`, etc. — across ~10 migrations. The token's `permissions` claim would carry the new slugs, so every literal must be updated in lockstep (a new migration redefining the helpers and any inline predicates). This is the real cost. |
| **Live WorkOS state × N environments**  | Permissions are environment-level in WorkOS; the slug is stored there and embedded in the token. Renaming means re-creating/renaming permissions and re-pointing role→permission grants in **every** firm's project (`ensure_workos_permissions`, `set_organization_role_permissions`).                                                                               |
| Data migration                          | `auth.permissions.slug`, `auth.role_permissions`, `auth.api_keys.scopes[]` and any stored slug references need a mapping migration.                                                                                                                                                                                                                                   |
| Generated TS constants                  | Regenerate; portals import constants, so only literal-string slug usages (grep) break.                                                                                                                                                                                                                                                                                |

### The observation that reframes B

**The client portal uses zero permissions today.** Every declared permission in `permissions.py` is
`portal="platform"` or `portal="operations"` — there is **no `portal="client"` permission anywhere**.
Client access is governed by RLS `contact_scope()`/`account_scope()`, not by an RBAC check (the code
comments on `tasks`/`messages` say so explicitly: "the client portal needs NO permission"). So:

- A `client:` slug prefix would name a namespace with **no current members**.
- There are **no cross-portal slug collisions today** (no resource is defined in two portals), so the
  disambiguation that portal-prefixing buys has no present-day case to solve.

The genuine benefits of B are therefore forward-looking: (a) making the `OPERATIONS_ONLY` separation
_structural in the slug_ rather than a lookup on the `portal` column, and (b) pre-empting a future
collision if a resource ever exists in two portals. Both are real but modest, and both are already
partly served by the existing `portal` **attribute** (which `rbac.OPERATIONS_ONLY` and
`push_roles_to_workos` already read).

### Recommendation on B

Weigh the SQL-literal + live-WorkOS churn against a benefit that is mostly hypothetical today. Options,
cheapest first:

1. **Keep the `portal` attribute; don't prefix the slug.** Zero migration; the separation already
   works. (Status quo.)
2. **Prefix only where a real collision arises** (i.e. when a resource genuinely lands in two portals).
   Least churn for the actual need.
3. **Full `portal:resource:action`**, done **only alongside Change A** (see below) so the SQL/WorkOS
   state is migrated once, not twice.

I'd not do B on its own for its own sake; the RLS-literal churn is the tell.

---

## Interaction & sequencing

The two changes share a theme — **portal becomes a first-class thing** — and they touch the same
seams (the `portal` claim, `push_roles_to_workos`, the RLS helpers, live WorkOS state). That argues
for doing them **together** if B is done at all, so the SQL migration and the per-environment WorkOS
reconcile happen once.

Recommended order:

1. **Done:** the documentation-accuracy pass ([`auth.md`](auth.md) + the `auth.py` comments).
2. **Done:** the WorkOS feasibility questions are answered from the OpenAPI spec (above) — apps are
   dashboard-only; access gating stays ours; roles/permissions don't move.
3. **Decide A1 vs A2** — the isolation-preserving path with a per-firm manual app step, or the
   canonical WorkOS shape that unwinds project-per-tenant. Everything downstream depends on it, so it
   is a product/architecture call, not an implementation detail. Confirm with WorkOS whether a
   non-public API for AuthKit applications exists (it would soften A1's ops cost).
4. **Change A as expand/contract**, ending with the RLS DENY lever keyed on a `portal` claim derived
   from the token's application (`client_id`/`aud`) rather than `auth.role() = 'client'`.
5. **Change B only if it rides A's migration** — one SQL-literal update, one WorkOS reconcile. Not
   blocked by WorkOS (slugs are free-form), but its cost is the RLS literals, and the client portal
   has no permissions to prefix today.

## Resolved by the OpenAPI spec (no longer open)

- ~~Are AuthKit applications creatable via the management API?~~ **No** — dashboard-only in the public
  spec (plan for manual per-firm setup under A1; confirm any private API with WorkOS).
- ~~What enforces per-application access with shared users?~~ **Nothing in the API** — it stays our
  membership/role check plus per-application dashboard config.
- ~~Are roles/permissions environment- or application-scoped?~~ **Environment-level permissions,
  environment/organisation-level roles** — unaffected by the org→app move.
- ~~Do WorkOS slugs allow `portal:resource:action`?~~ **Yes** — slugs are free-form unique strings
  (verify by creating one).

## Open questions to resolve before coding

- **[A, decision]** **A1 or A2?** Keep project-per-tenant with a manual per-firm app step, or collapse
  to org-per-firm + shared portal apps and unwind project-per-tenant? (See [the fork](#the-fork-the-findings-expose-a1-vs-a2).)
- **[A]** Under A2 specifically: what happens to the `auth.users` `(tenant_id, email)` identity model
  when a whole stage shares one WorkOS user pool?
- **[A]** Is per-application AuthKit branding rich enough to keep white-label sign-in under A2 (or is
  that a reason to prefer A1)?
- **[A]** Per-portal session policy: a shorter platform-staff session vs a longer client session, now
  that applications make it configurable per surface — a concrete product win to design in.
- **[B]** Is the `system` slug left global (unprefixed), or `operations:system`?
- **[B]** Do we introduce `client:*` permissions at all, given the client portal is RLS-gated with no
  RBAC today?

## References

- [WorkOS OpenAPI spec (authoritative — the source for the resolved findings above)](https://raw.githubusercontent.com/workos/openapi-spec/refs/heads/main/spec/open-api-spec.yaml)
- [Applications – AuthKit – WorkOS Docs](https://workos.com/docs/authkit/applications)
- [Users and Organizations – AuthKit – WorkOS Docs](https://workos.com/docs/authkit/users-organizations)
- [Multiple apps, one shared authentication layer — WorkOS](https://workos.com/blog/multiple-apps)
- [Staging vs. production environments – AuthKit – WorkOS Docs](https://workos.com/docs/authkit/environments)
- [The developer's guide to JWKS — WorkOS](https://workos.com/blog/developers-guide-jwks)
- [JWT validation: how-to and best libraries — WorkOS Guides](https://workos.com/guide/jwt-validation)
- [Applications – API Reference (WorkOS **Connect** — note: distinct from AuthKit applications)](https://workos.com/docs/reference/workos-connect/applications)
