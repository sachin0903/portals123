# Graphene Platform — Identity & Access Decision Record

**Status:** Accepted June 2026; **materially revised July 2026 → project-per-tenant** (white-label
identity isolation — see the **Revision** note below, which supersedes the org-as-tenant parts of §1
and reframes §3–§6). Client feature-gating remains entitlement-based (§3). Records the
first-principles identity/authorisation model the schema and migrations implement; supersedes the
`project-context.md` §3–4 sketch (which used `app.*` GUCs and a single `clients` table), in the
WorkOS + pre-resolved-claims idiom wired into `apps/api/app/{auth,db}.py`.
**Companion files:** [`data-architecture.md`](./data-architecture.md) · [`auth.md`](./auth.md) (login flow) ·
[`api.md`](./api.md) (the RLS claims contract) · `apps/api/migrations/sql/0001_auth_core.sql`.

**Implementation status:** the identity/RLS foundation is **built and tested in `0001_auth_core`**
(the `auth` schema, the helpers below incl. `auth.resolve_claims`, and the request-layer wiring).
The client-scope claims (`contact_ids`/`account_ids`) resolve to empty until **`0002`** adds the
`crm` tables they derive from. See §6–7.

---

## 0. Revision (July 2026) — project-per-tenant (white-label isolation)

**Decision.** Graphene is sold as each firm's _own_ platform, not a Graphene product exposed to a
firm's customers. Clients and staff of different firms must therefore **never share login details**.
Since WorkOS identities (email) are unique **per environment**, the tenant boundary moves from the
WorkOS _organisation_ to a **WorkOS project per tenant** — each firm gets its own project, with the
usual **staging + production environments inside it**. This is the maximal-isolation end of WorkOS's
"single-tenant" spectrum, chosen deliberately over the default org-as-tenant model. Viable here
because tenants are **tens of high-touch adviser firms**, onboarded manually — not self-serve at
scale. (If that ever changes, per-tenant WorkOS projects are the wrong primitive — revisit against a
per-tenant-realm IdP such as Cognito/Keycloak/Auth0, which _are_ API-provisionable.)

**WorkOS structure.** Tenant = **project**; stage = **environment**; portal = **organisation**.

```
WorkOS account: Graphene
  Project: «Tenant»                     — one per firm (top-level isolation boundary)
    Environment: Production   ─┐ one identity store per stage; email unique within it
    Environment: Staging      ─┘ ⇒ unique per tenant, natively
      └ Organisations (within each environment):
          • Client Portal  — end clients   (role: client)
          • Platform       — firm staff    (adviser / paraplanner / compliance / firm_admin)
  Project: Graphene Ops                   — the admin/GIO portal's own project
    Environment: Production / Staging
```

**What this changes vs the model documented below (this section wins on conflict):**

- **Tenant = project; stage = environment; the `org_id` claim now means _portal_ (Client vs
  Platform), not tenant.** `tenant_id` is derived from **which project/environment authenticated the
  request** (token issuer / verifying `client_id`), never from `org_id`. §5–§6 predicates are
  unchanged; only the _source_ of `tenant_id`/lineage changes.
- **Email is unique per tenant, natively** — the original "unique within the tenant" requirement is
  now automatic (unique per environment = per tenant). **Reverses** the June "one-login-per-person"
  note: a person who is a client/staff at two firms has **two separate logins**, one per environment.
- **Dual capacity stays clean.** A firm staffer who is also a client of _that same firm_ is **one**
  login in that environment with memberships in **both** the Platform and Client Portal orgs
  (WorkOS's one-role-per-membership is why two orgs beats one-org-with-roles). The `crm.contact_users`
  bridge (§2) still models login↔party _within_ the tenant.
- **RBAC (§4) is seeded per environment.** Roles/permissions/branding don't carry across
  environments, so the template seed + reserved-permission grants run **once per new tenant
  environment**, against that environment's API key. `contacts:read_all` etc. work exactly as in §4,
  just scoped within the environment.
- **Provisioning is a manual runbook** — WorkOS has no API to create a project/environment. Onboarding
  a tenant = operator creates the **project** (+ its production/staging environments), configures
  AuthKit (branding, custom domain, redirect URIs), creates the two orgs, then **registers the
  environment in the admin UI** (pasting its client_id / issuer / host + api key / cookie password)
  and seeds roles via that env's key. The admin portal's old "create tenant = create org via API"
  step is gone. **Confirmed manual** (no project-creation API) and accepted —
  tenant onboarding is already a 3–4-step high-touch implementation per firm.
- **Shared, host-keyed deployment (decided July 2026).** One deployment per portal (client,
  platform) + one shared API — consistent with the shared RLS DB. The **portal** resolves
  tenant→environment by **request host**; the **API** resolves it by token **issuer** (built —
  `app/tenant_registry.py`). Both map to the same `auth.tenant_environments` row; branding is
  data-driven by host (`auth.tenants.theme/logo`). `getWorkOS()` becomes host-keyed (`pkgs/auth`
  - each app's `src/lib/server/workos.ts`), using the environment matching the deployment's own stage.
    Per-tenant deployment stays available as an escape hatch for a tenant that demands process isolation.
- **Admin / GIO portal** lives in its **own project** and manages tenant projects server-to-server via
  their API keys (a fan-out); it is _not_ a cross-tenant member. The WorkOS sync worker likewise runs
  **per tenant project/environment**. Each environment has its **own keys**; an **operator sets them
  in the admin UI** (after creating the project/environment in WorkOS). Keys are stored **encrypted at
  rest** in the DB (a service_role-only `auth.environment_secrets` table — api key + cookie password
  as ciphertext), decrypted at runtime with a **master key held in env/KMS** (the one bootstrap secret,
  not UI-managed). This trades an external secret manager for encrypted-DB + master-key: simpler ops,
  and a DB dump alone does not leak keys. **The API is the sole secret authority** — it holds the
  master key and owns the DB; the shared portal, which needs the cookie password to seal/unseal
  sessions, fetches its per-host WorkOS config from an **internal, service-authed API endpoint** and
  caches it (it never holds the master key or touches the DB). Full API-proxy of auth is not viable
  because sealed sessions live in the portal.
- **DB stays one shared Postgres + RLS.** Identity isolation is at WorkOS; RLS remains the data
  backstop with `tenant_id` sourced from the environment. Most of **`0001_auth_core` stands**
  (memberships, RLS helpers, audit) — but two concrete changes: **(a)** `auth.users` gains a
  tenant/environment discriminator and its `email` uniqueness moves from **global** to
  **`UNIQUE(tenant_id, email)`** (the same email now legitimately recurs across tenants in the shared
  table; `workos_user_id` stays globally unique); **(b)** `auth.resolve_claims` + the request-layer
  wiring source tenant from the environment and treat `org` as portal.
- **Commercials/limits** — WorkOS is **not** billed per-environment and there is **no known cap** on
  projects-per-account; the team owns the commercial model (not assumed here).

The layers below (three concepts, the `contact_users` bridge, client-as-org-member, entitlements, the
two-layer RLS predicates, the claims-contract mechanics) all still hold **within a tenant**; read them
with "tenant = project, stage = environment, org = portal" substituted.

---

## 1. First principles — three concepts, not one

Identity has three _distinct_ concerns that earlier drafts half-conflated into `auth.users` +
`crm.contacts.user_id`. Separate them and every hard case becomes mechanical:

| Concept                                 | Table                           | Answers                                                           | Scope                                                                                                                       |
| --------------------------------------- | ------------------------------- | ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| **Login / principal**                   | `auth.users`                    | "who authenticated" (identity only)                               | one row per WorkOS user; email unique **per tenant** (§0); tenant = environment                                             |
| **Membership = (person, portal, role)** | `auth.organisation_memberships` | "is a member of firm X's _portal_ P, as role R"                   | `UNIQUE(user_id, tenant_id, portal)`; a person may hold Client **and** Platform independently; one **active** org per token |
| **Party / contact**                     | `crm.contacts`                  | "the domain person/holder — the _client as data_ (KYC, accounts)" | tenant-scoped                                                                                                               |

- **WorkOS owns identity** (authN + the `role`/`permissions` claims); **this app owns authorisation**
  (RLS + the app authz layer). One WorkOS user → one `auth.users` (by `workos_user_id`).
- A person may be a member of **many** WorkOS orgs; the token carries exactly **one active `org_id`**,
  resolved server-side (+ lineage). _(⚠ Per §0: within a tenant environment those orgs are **portals**
  — Client Portal / Platform — not other tenants. `tenant_id` comes from the project/environment, not
  `org_id`. A person at two firms has **two logins**, one per tenant project.)_
- Roles (`adviser`, `paraplanner`, `compliance`, `firm_admin`, `client`, plus `system`/`admin` for
  Graphene staff) are the _role on a membership_, surfaced as JWT claims — never a column on `users`.
  `platform.advisers` is only the adviser's regulatory profile (IRN), not the role.
- **`auth.users` is pure identity:** `irn`/`title` do **not** live on it (they moved to
  `platform.advisers` in `0002`); `auth.uid()` resolves the `user_uuid` claim to `auth.users.id`.

## 2. The login → party bridge — `crm.contact_users` (many-to-many)

The single `crm.contacts.user_id` column is **replaced** by an explicit join, because the real cases
are many-to-many:

```
crm.contact_users (contact_id, user_id, capacity in {self, authorised, power_of_attorney, delegate})
  UNIQUE (contact_id, user_id)
```

| Case                                          | Modelled as                                                                                                                                                                             |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Elderly couple, one shared email**          | WorkOS forbids two logins on one email → **one** `auth.users`; **two** `crm.contacts`; two `contact_users` rows (`self`). The login's scope = both contacts.                            |
| **Adviser who is also a client**              | one login; an `adviser` **membership** (+ `platform.advisers`); _separately_ a `crm.contacts` row linked via `contact_users` (`self`). Staff-ness and client-ness are different layers. |
| **POA / delegate (one contact, many logins)** | the contact's own `self` login **plus** a delegate login (`power_of_attorney`/`delegate`).                                                                                              |

Email uniqueness lives on the **login** (`auth.users.email`), never on `crm.contacts.email`.

## 3. Clients are WorkOS org members; staff assignment is `crm.client_advisers`

- **Client end-users are members of the firm's WorkOS org** with the reserved role **`client`**, so
  the same `org_id → tenant_id` claim flow serves staff and clients — one auth model.
- **Staff ↔ client assignment** (a client's _team_; an adviser's "book") is
  `crm.client_advisers (contact_id, adviser_id, role)`. Since `0245`, `role` is one of `adviser`,
  `paraplanner`, `administrator`, `support`, and a client has **at most one adviser**
  (`client_advisers_one_adviser_key`) plus any number of other staff. Only an
  `auth.users.is_adviser` login may be the adviser; any other role takes any member of staff
  (`contacts.assign_adviser` checks the person, the CHECK checks the word). Naming a new adviser
  moves the old one to `support`; switching off `is_adviser` does the same to every client they
  advised. "The client's adviser" is therefore a lookup — `role = 'adviser'` — never an
  `ORDER BY … LIMIT 1`. Every row, whatever its role, scopes an own-book login when they are _not_
  granted firm-wide read.
- **Advised vs execution-only** is a first-class, action-gating attribute on the client (a typed
  `service_type` on `crm.contacts`, landing in `0002`) — not a role and not buried in jsonb.
- **Client feature-gating is _entitlements_, not RBAC (decided July 2026).** RBAC permissions are a
  **staff** concept (what an adviser/compliance user may _do_); clients are never granted permissions.
  To gate portal features for groups of clients, resolve **entitlements** as a **tenant default (the
  firm's plan) overridden by per-client entitlements** — client-level, not user-level (a login linked
  to two clients sees each client's entitlements in that client's context). Entitlements govern
  _feature availability_ in the client portal; any sensitive **action** stays enforced server-side +
  RLS regardless. Lands in a later migration (not `0001`/`0002`); shape it alongside the tenant "plan"
  concept when the first gated feature appears. Distinct from `service_type` (which gates the service
  _model_, advised vs execution-only).

## 4. RBAC: roles & permissions are **per-tenant configurable** (DB-only; never pushed to WorkOS)

Brought forward from the deferred mirror (we need them now for client scope + assignment + the admin
RBAC UI). **This app is system of record, exclusively** — a role's permissions are written here
directly (`app/rbac.py::set_org_role_permissions`/`create_org_role`) and are **deliberately never
pushed to WorkOS**: WorkOS's AuthKit embeds a role's assigned permissions straight into every access
token it mints for that role, and this registry grew large enough that doing so pushed some roles'
sealed session cookies past the browser's ~4096-byte ceiling (`Cookie "wos-session-*" is too large,
and will be discarded by the browser`; see `docs/auth.md`). Role **objects** still get created in
WorkOS (`role`/`roles` ride the token harmlessly, a few bytes each, and are useful for
telemetry/display) — only permission _assignment_ stopped, at every write path
(`push_roles_to_workos` on new-tenant setup included). `sync_org_roles_from_workos` (the reconcile
FROM WorkOS) mirrors role existence/metadata only, never permissions, for the same reason in
reverse — WorkOS's `role.permissions` is no longer a trustworthy signal of anything once nothing
ever writes it there. Runtime authz reads **these tables** on every request —
`auth.member_permissions` (0093), called from `app/auth.py::get_current_principal` and then
scoped by `permissions.for_portal` so operations-only slugs including `system` cannot leak
into a firm-portal session — never a JWT claim.

```
auth.roles (id, workos_role_id, tenant_id NULL=environment/global template, slug, name,
            type in {environment, organisation}, ...)  UNIQUE NULLS NOT DISTINCT (tenant_id, slug)
auth.permissions (id, slug 'resource:action', ...)  UNIQUE(slug)
auth.role_permissions (role_id, permission_id)  PK(role_id, permission_id)
auth.users (..., type text[] subset of {client, staff})
auth.organisation_memberships (id, workos_membership_id, user_id, tenant_id,
                               role_id [primary/cosmetic], portal, status)
auth.membership_roles (membership_id, role_id)  PK(membership_id, role_id)
```

`auth.users.type` is a database-enforced boundary, not a presentation filter. `[staff]` identities
may hold only Platform/Operations membership; `[client]` Profiles may hold only Client membership;
an empty array is a WorkOS import until a deliberate grant claims it. `[client, staff]` represents
only the identities grandfathered by `0120`; a singleton/untyped identity cannot become dual.
`access_kind` remains temporarily as a synchronised rolling-deploy compatibility column. The
permission-bearing role set is `membership_roles`; `resolve_claims` unions every selected role.

Two details of that boundary are `0120`'s, and both were learned by breaking the dev book (its
header is the full account, and the lesson worth carrying: **DML in an `auth` migration must run as
`service_role`** — a migration runs as `migrator`, `auth.*` carries FORCE RLS, FORCE binds the
owner, so as `migrator` every `auth.*` table reads as empty. 0119's backfill inserted nothing and
its two `RAISE EXCEPTION` guards were dead: a guard that reads no rows does not refuse, it passes,
and every session lost its permissions the moment `resolve_claims` started reading the empty
bridge table).

- **A role must match its membership's client/staff SIDE, not its exact portal.** The escalation
  worth a trigger is a client Profile reaching a staff role, whose permissions
  `auth.member_permissions` would union into a client session (real: one client membership on the
  dev book carried the 68-permission `platform-admin`). Platform-versus-operations drift among firm
  staff is pre-existing data that granted exactly what it grants now, and refusing it at migration
  time cannot repair it — it can only decline to backfill, which reads as "all staff lost their
  permissions on deploy". `provisioning.validate_member_role_set` still requires an exact portal
  match for every new assignment, so the drift cannot grow.
- **An identity that already held both sides is grandfathered**, left unclassified rather than
  contradicted: splitting one needs a second address and an invitation that no migration can
  invent. It keeps what it holds and appears under BOTH Team and Profiles (`members_with_portals`
  matches an unclassified identity by the side it holds a membership on) so an operator can find
  and split it. Every single-sided identity is classified, and every new one is classified by its
  first grant, so the boundary holds everywhere it can be stated.

**Deployment stage is per-instance, not per-tenant.** dev / staging / production are separate
deployments (each its own DB); a tenant therefore has **one** `auth.tenant_environments` row per
instance, stamped with the instance's `APP_STAGE` (`config.settings.app_stage`). The admin tenant
UI edits that single config; `environments.set_environment` stamps the stage (callers don't pass
one). The `stage` column + CHECK (`dev|staging|production`) stay for record-keeping.

**Firm self-service.** Firm staff manage staff under **Team**, client-access identities under
**Profiles**, and role definitions from the **platform portal** via the `/firm/*` API
(`firm_session` + `auth.require_permission`),
gated by the seeded `members:read` / `members:manage` / `roles:manage` permissions (a firm-admin
role on the model tenant carries them, so clones inherit a working admin role). Operations stays
Graphene-internal (admin `/operations`), never exposed to firms.

- **"See all" vs "own book" is a _permission_, not hardcoded SQL.** The reserved permission
  **`contacts:read_all`** grants firm-wide visibility. Each tenant grants it to whichever roles it
  wants — Firm A gives it to everyone; Firm B gives it to paraplanners/compliance but **not** advisers
  (who then see only their assigned book). Pure configuration; no migration, no SQL change.
- **`type='environment'` (tenant_id NULL)** roles are Graphene-provided **templates** (`client`,
  `adviser`, `paraplanner`, `compliance`, `firm_admin`); `0001` seeds them + the reserved permission
  - default firm-wide grants to paraplanner/compliance/firm_admin. **New-tenant setup** = clone the
    template set into `organisation` roles (or assign the environment roles directly). The
    `UNIQUE NULLS NOT DISTINCT` constraint is what makes the NULL-tenant templates unique by slug.
- Fine-grained "_this_ adviser may touch _this_ application" stays at the app layer (WorkOS FGA /
  Warrant), **not** mirrored for RLS.

## 5. The RLS contract — two layers, claim-driven, fail-closed

RLS is the data-scoping backstop; the app authz layer enforces the _action_ on top (belt & braces).
Everything is read from **pre-resolved claims** (zero per-row queries).

**The key lever:** `auth.tenant_lineage()` returns **empty for a `client` principal**. An empty
lineage fails _every_ existing `tenant_id = ANY(auth.tenant_lineage())` policy, so a client is locked
out of **all** staff/tenant tables automatically — reachable **only** through the explicit
contact/account-scoped policies below.

Helper functions (`auth.*`, all `STABLE`, read claims, fail-closed) — **implemented in 0001**:

| Helper                  | Returns | Source                                                                              |
| ----------------------- | ------- | ----------------------------------------------------------------------------------- |
| `auth.uid()`            | `uuid`  | the `user_uuid` claim (→ `auth.users.id`)                                           |
| `auth.tenant_id()`      | `int`   | the `tenant_id` claim (psql fallback: lookup by `workos_org`)                       |
| `auth.tenant_lineage()` | `int[]` | `'{}'` when `role='client'`; else the `tenant_ids` claim (staff firm + descendants) |
| `auth.can_read_all()`   | `bool`  | `auth.has_permission('contacts:read_all')` (also true for `system`/`admin`)         |
| `auth.contact_scope()`  | `int[]` | the `contact_ids` claim — contacts this login may see; `'{}'` if absent             |
| `auth.account_scope()`  | `int[]` | the `account_ids` claim — accounts this login may see; `'{}'` if absent             |

**Predicate shapes** — only the _client-readable_ tables (in `0002+`) take the two-layer form;
staff-only tables keep the plain lineage gate (which already excludes clients):

```sql
-- client-readable tables (crm.contacts by id; platform.accounts by id;
-- platform.transactions / pensions.* / payment_runs by account_id):
USING ( (tenant_id = ANY(auth.tenant_lineage()) AND auth.can_read_all())
        OR <scope_col> = ANY(auth.<contact|account>_scope()) )
-- WRITE: gated by lineage (empty for clients => client read-only at the DB) + scope:
USING/CHECK ( tenant_id = ANY(auth.tenant_lineage())
              AND (auth.can_read_all() OR <scope_col> = ANY(auth.<...>_scope())) )
```

Resolves all four principals: **read-all staff** → whole firm (branch 1); **own-book adviser** →
assigned book (branch 2); **client** → their contacts/accounts (branch 2; lineage empty → branch 1
dead → **read-only**); **cross-firm** → scope arrays only ever contain the caller's own tenant's ids.

**Improvement applied in 0001 (divergence from the original sketch).** `auth.users` is **not**
`USING(true)`. A client could otherwise read the entire cross-tenant user directory (every email,
mobile, name). Its read policy is now **same-tenant + self**:

```sql
-- auth.users SELECT (authenticated):
USING ( id = auth.uid()
        OR EXISTS (SELECT 1 FROM auth.organisation_memberships m
                   WHERE m.user_id = auth.users.id AND m.tenant_id = ANY(auth.tenant_lineage())) )
```

A `client` (empty lineage) sees only their own row; staff see their firm's users. `auth.tenants`
**stays `USING(true)`** deliberately — scoping it would recurse, because `auth.tenant_id()` /
`auth.tenant_lineage()` read `auth.tenants` themselves. Writes to `auth.users`/`auth.tenants` are
`service_role`-only (WorkOS sync); there is no `authenticated` write policy, so app writes fail closed.

## 6. The request-layer claim contract (app responsibility) — **WIRED**

`apps/api/app/db.py::tenant_session` establishes the RLS context per request:

1. cheap fail-closed if the token carries no `org_id`;
2. `SET LOCAL ROLE authenticated` (the `app` login role is a member, so `TO authenticated` policies apply);
3. `SELECT auth.resolve_claims(:workos_user, :workos_org)` — the **`SECURITY DEFINER`** minter that
   resolves the WorkOS identity into the internal claims;
4. merge with `auth.py::build_jwt_claims` (the JWT-derived half: `sub`, `role`, `permissions`, `sid`,
   `org_id` — `role`/`sub`/`sid`/`org_id` come straight from the verified token, but `permissions`
   is resolved fresh from the DB by `get_current_principal` (`auth.member_permissions`, 0093)
   _before_ `build_jwt_claims` runs, precisely so break-glass's `system` grant — appended to
   `principal.permissions` in Python — still lands in `request.jwt.claims` and SQL's
   `auth.is_system()` agrees with it; see §4 for why the token no longer carries it at all);
5. fail closed (403) if `user_uuid` or `tenant_id` didn't resolve;
6. `SELECT set_config('request.jwt.claims', :claims, true)` — transaction-scoped (PgBouncer-safe).

`auth.resolve_claims(p_workos_user_id, p_workos_org_id) RETURNS jsonb` mints, beyond today's
`{sub, role, permissions, sid, org_id}`:

- **`user_uuid`** — `auth.users.id` for the WorkOS user.
- **`tenant_id`** + **`tenant_ids`** — the active tenant and its lineage (firm + descendants).
- **`contact_ids`** — for a `client`: their `contact_users` contacts; for an own-book adviser: their
  `client_advisers` contacts. (Read-all staff: omitted/empty — branch 1 covers them.)
- **`account_ids`** — the accounts owned by those contacts (`platform.accounts.contact_id`).

In `0001` the function resolves the auth-level claims and returns **empty `contact_ids`/
`account_ids`** (the `crm`/`platform` tables don't exist yet); `0002` does `CREATE OR REPLACE` to
fill the client scope. Fail-closed: no claim → empty scope → the client sees nothing, never the firm.

**Why `SECURITY DEFINER` + a BYPASSRLS owner.** The minter must read `auth.users`/`auth.tenants`
_before_ any claims exist (chicken-and-egg with the scoped `auth.users` policy). It is therefore
`SECURITY DEFINER` and — because `FORCE ROW LEVEL SECURITY` binds even the table owner (`migrator`)
— **owned by the BYPASSRLS `service_role`** so it can read regardless of policy. The same applies to
the lineage triggers and `audit.record()`. Membership of `service_role` does **not** leak BYPASSRLS
to the `migrator` itself (role attributes aren't inherited), so `FORCE RLS` stays meaningful.

## 6a. View as — a member of staff in the client portal, read-only (`0232_view_as_sessions`)

The question behind half the calls a firm takes is "what are you seeing?". View-as answers it:
from the client record ("View their portal", `contacts:read`) a member of staff gives a reason
and a new tab opens the firm's client portal **as that client**, read-only, for up to an hour.

**The session is a row, and the row is the credential.** `auth.view_as_sessions` holds the
SHA-256 of an opaque `viewas_…` token (never the token), who opened it (`adviser_id`), whom it
shows (`contact_id`), the reason, and the clock (`expires_at`, `ended_at`). A row rather than a
signed token because it has to be **revocable on the next request**: `GET /view-as/session` is
asked on every client-portal request and "Leave" (`POST /view-as/end`) stops it immediately.

**The handoff.** `POST /view-as/open/{contact_uid}` (`firm_session`, platform portal only, the
client in scope under the caller's own RLS) mints the row and returns a single-use
`viewas-handoff_…` ticket plus the firm's client-portal base URL (`client_portal_base_url`, else
the tenant's `client_portal_host`). The platform opens `{portal}/view-as?ticket=…`; the client
portal's server redeems it (`POST /view-as/redeem`, internal token, not in the schema) for the
token, which lands only in the httpOnly `gp-view-as-client` cookie with `maxAge` = the session's
remaining seconds. A ticket works once; a forwarded link is inert.

**The claims can only narrow.** `app/auth.py::get_current_principal` recognises the token
prefix, resolves the live row under `service_role`, and issues a principal whose `user_id` is the
**adviser's** WorkOS subject, `portal='client'`, `permissions=[]`, and `view_as_contact_id` set.
`tenant_session`/`firm_session`/`portal_session` then call
**`auth.resolve_view_as_claims(user, org, contact_id)`** instead of `auth.resolve_claims`. It
runs the ordinary platform resolution for the login first (looking the firm's
`platform_client_id` up from `auth.tenant_environments`) and refuses unless the result already
reaches the contact — in `contact_ids` (own book) or holding `contacts:read_all`/`system` — and
the contact is live in that tenant. Only then does it mint `{portal:'client', tenant_ids: [],
contact_ids: [c], account_ids: <c's>, role_ids: [], view_as: c}`: the shape a client's own login
has, for exactly one client. `auth.view_as()` reads the claim (NULL for every ordinary session).
`auth.uid()` stays the adviser, so a "this login's own party" check (`capacity = 'self'`) does
not name the client on screen. The client portal's profile reads that client from the view-as
session's contact id, the single id in `contact_ids`.

**Two policies were re-cut.** `crm.contact_users` and `crm.client_advisers` had bare
`user_id = auth.uid()` / `adviser_id = auth.uid()` branches — fine for a real login, but the
view-as principal's uuid IS the adviser's, so a session captioned with one client's name would
have listed the adviser's whole book. Both branches are now `auth.view_as() IS NULL AND …`. Every
other client-reachable policy already goes through `contact_scope()`/`account_scope()` or
`tenant_lineage()` (empty under view-as). `tests/test_view_as.py` holds this from both ends.

**Read-only is enforced by method, not by a list of routes.** `view_as.permits(method, path)`
allows GET/HEAD/OPTIONS and only `POST /view-as/end`; anything else is 403 "You cannot take this
action on behalf of the client." before the handler runs — so a mutating route that ships next
month is covered the day it lands. The client portal installs a `window.fetch` guard
(`view-as-guard.svelte.ts`) for that session only, which turns the refusal into a dialog; it hides
the bell, the contact button and the page-view beacon, and it draws a permanent brand bar
naming the client with the time left. `view-as` is a reserved portal-page slug.

**Audit.** `client.portal_viewed_as` (open, with the reason in the request) and
`client.portal_view_ended` are action events under the adviser's name; any event a view-as
session does write carries `metadata.view_as = {session_uid, contact_id}`. The row itself is
`audit.record()`-triggered and readable by its adviser or anyone with `audit:read` in the firm.

Settings: `view_as_session_minutes` (60), `view_as_handoff_seconds` (120).

## 7. Migration placement

- **`0001`** ✅ — `auth.users`/`tenants` (+ lineage trigger with cycle guard **and descendant
  recompute on re-parent**), the RBAC mirror (un-deferred), all the helpers above incl.
  `can_read_all`/`contact_scope`/`account_scope` + the `tenant_lineage` client lever +
  `resolve_claims`, the reserved-permission/role seed, `auth.api_keys`, the append-only `audit.log`
  - `audit.record()`/`audit.attach()`, mirror RLS (read tenant-scoped, write `service_role` only),
    and the scoped `auth.users` read policy.
- **`0002`** (pending) — `crm.contact_users` (replaces `crm.contacts.user_id`) + `crm.client_advisers`
  - `account_contacts` (with a key) + `service_type`; the two-layer client-scoped RLS on
    `crm.contacts` / `platform.accounts` / `platform.transactions`; `auth.resolve_claims` re-defined to
    fill `contact_ids`/`account_ids`; the DML audit triggers on the sensitive domain tables.
- **`0003`/`0004`** — two-layer RLS on the account-bearing client-readable tables (`pensions.*`,
  `platform.payment_runs`); ops/system tables (`reconciliation_breaks`, `sync_jobs`,
  `transaction_expectations`, `income_instructions`) stay staff-scoped (clients blocked by lineage).

## 8. Deferred / out of scope (consciously)

WorkOS `apps`, `organisation_domains`, `sessions` mirrors (land with the device-management /
SSO-routing UI); WorkOS FGA/Warrant for fine-grained resource grants (app-layer, not mirrored); the
WorkOS→DB sync worker itself (this DR defines the target tables it writes); MFA-by-role enforcement
(needs orgs/roles wired — `project-context.md` §9).
