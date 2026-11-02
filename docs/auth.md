# Authentication & authorisation (WorkOS, custom UI)

How identity and access work across the three portals (`apps/client`, `apps/platform`, `apps/admin`)
and the FastAPI backend (`apps/api`). WorkOS owns **identity** ("who is this user"); this application
owns **authorisation** ("what may they do"), enforced in the API and in Postgres Row Level Security.

The shared, framework-agnostic logic lives in `@graphene/auth` (`pkgs/auth`); each portal wires it to
SvelteKit with a thin `src/lib/server/` glue layer. See also [`identity-and-access.md`](identity-and-access.md)
for the decision-level model, [`api.md`](api.md) for the backend, and
[`auth-applications-migration.md`](auth-applications-migration.md) for the **planned** move of the portal
boundary from WorkOS organisations to WorkOS applications (not yet built).

> **British English throughout. This document describes what is built today.** Where a behaviour is
> known-imperfect it is called out under [Known gaps](#known-gaps) rather than glossed.

## The model in one screen

- **Project-per-tenant.** Each adviser firm is its own WorkOS **project/environment**, with its own
  client id, JWKS (token-signing keys), API key and session-cookie password. Clients and staff of
  different firms never share a login; the same email at two firms is two separate logins
  (`auth.users` is unique on `(tenant_id, email)`, not on email alone).
- **Application-per-portal, ONE firm org.** Each portal (Client, Platform, and — Graphene only —
  Internal Operations) is its own WorkOS **application** with its own client id + client secret; a
  session signs in through its portal's application into the firm's **single org** (`firm_org_id`).
  **Which portal a person may use lives exclusively in `auth.organisation_memberships`** — never in
  WorkOS orgs. Since `0121`, the shared identity classification is `auth.users.type text[]`:
  `[staff]` may hold only Platform/Operations membership; `[client]` may hold only Client
  membership; an empty array is a WorkOS import not yet deliberately claimed. Database triggers
  enforce both directions, so changing a UI or bypassing provisioning cannot cross the boundary.
  `[client, staff]` is reserved for identities that already held both sides when the boundary
  landed (grandfathered by `0120`, keeping what they hold and listed under both Team and Profiles
  until a human splits them) — a migration cannot invent the second email address a split needs.
  New identities cannot become dual. `access_kind` remains as a synchronised rolling-deploy
  compatibility column and is not the source of truth. The
  token's `client_id`
  claim names the application, and `auth.resolve_claims` grants that portal only when the matching
  active membership row exists; otherwise the portal is NULL and the request layer refuses
  ("This account has no access to this portal"). When the same application client id is stored on
  more than one portal, the request may name its portal with the `x-graphene-portal` header
  (`request.portal`). That name is used only when the token's application is stored on that
  portal and the login holds an active membership there; a hint for a different application is
  ignored. With no usable hint, the membership decides, in the order client, then platform,
  then operations, so a staff login stays on the platform unless the request names operations.
  Each portal installs the header on its API calls. There is no org-based classification, no
  fallback, and no default.
- **Roles are sets.** `auth.membership_roles` attaches one or more roles to a membership;
  `auth.resolve_claims` mints all of their ids and runtime permissions are their union.
  `organisation_memberships.role_id` remains only the stable primary/cosmetic role WorkOS displays.
  A role must match the membership's tenant and its **client/staff side** — the crossing that
  escalates is a Profile reaching a staff role, while platform/operations drift among staff is
  pre-existing data (`0120`; `provisioning.validate_member_role_set` still demands an exact portal
  match for anything newly assigned, so the drift cannot grow).
- **No self-service signup.** Accounts are provisioned (admin/firm invite → WorkOS user + membership
  - revocable WorkOS Invitation → emailed set-password link). Every portal exposes login, MFA, set-password, forgot-password,
    logout and `account/security` — **no `signup` and no `verify` route** (see below).
- **Self-hosted auth pages.** WorkOS's own (AuthKit-hosted) pages and emails are **disabled**; all
  auth UI is our own SvelteKit routes, and transactional emails go via Resend (`app/email/transport.py`).

## The end-to-end flow

1. The user submits the custom `/login` form. The SvelteKit action resolves **which portal
   application** this host is (`resolveAuth(host)` → the API's `env_config_by_host`, which returns
   the host portal's application client id, that application's decrypted secret, the cookie
   password, the firm org and branding) and calls WorkOS `authenticateWithPassword` with the
   application's own credentials and the browser's IP address and user agent. Password auth is
   server-to-server, so WorkOS stores those on the session only when we forward them. The same
   pair goes out on organisation selection and on the TOTP calls that finish MFA and enrolment.
   **There is no fallback**: an unknown host, or a portal whose application client id is not
   stored, reads as 503 "temporarily unavailable" — never a silent sign-in against another
   project, and never "invalid email or password".
2. On success WorkOS returns a **sealed session** (the refresh token, iron-session-encrypted). It is
   stored in an httpOnly cookie, namespaced per portal (`wos-session-client|platform|admin`) so two
   portals on one host cannot cross-read each other's session.
3. On every request, `hooks.server.ts` loads the sealed session, **authenticates and refreshes** it —
   reactively when the access token has expired and **proactively** when it is within 60s of expiry
   (`@graphene/auth` `resolveSession`) — and puts the user, access token, org and role on
   `event.locals`.
4. When the SvelteKit server calls the API, it forwards the WorkOS **access token** (a JWT) as
   `Authorization: Bearer <token>`.
5. The API verifies the JWT against the **issuing environment's** JWKS (`app/auth.py`): it reads
   the token's `org_id`/`iss` unverified only to _select_ which environment's keys to use
   (`tenant_registry`; the org index is keyed on `firm_org_id`), then checks the RS256 signature and
   enforces `exp`/`sub` (and `iss`, when known). It builds a `Principal` whose `client_id` is the
   token's application, and whose **permissions come from the DB** (`auth.member_permissions` over
   the resolved portal's role, then `permissions.for_portal`) — never from a token claim.
   Operations-only slugs including `system` apply only on the operations portal, so a Graphene
   role attached to both memberships cannot make every firm `can()` true. The signature —
   per-project keys — is the real gate.
6. A **session dependency** then establishes the request's DB context (below) and the handler runs.

The frontend writes no auth logic beyond the forms; the backend trusts only verified token claims.

## Auth routes (per portal)

All three portals carry the same set. The shared _glue_ (`src/lib/server/{workos,auth,validation}.ts`,
`hooks.server.ts`) is thin and per-app by necessity (it touches `$env`/`$app`); the **route actions
themselves are currently byte-identical copies across the three apps** — see [Known gaps](#known-gaps).

| Route               | Purpose                                                                                 |
| ------------------- | --------------------------------------------------------------------------------------- |
| `/login`            | Password sign-in; branches to MFA / enrol / email-confirm; open-redirect-safe `?next=`. |
| `/mfa`              | TOTP challenge for a user with an enrolled factor.                                      |
| `/mfa/enroll`       | Login-time TOTP enrolment when the environment mandates MFA.                            |
| `/set-password`     | Set a password from an emailed reset/invite token (this also verifies the email).       |
| `/forgot-password`  | Request a set/reset link. No account-existence oracle in the response body.             |
| `/logout`           | Ends the WorkOS session and clears the cookie.                                          |
| `/account/security` | Opt-in TOTP enrol/confirm/disable; password change (re-auths, sweeps other sessions).   |

There is **no `/verify` route** — email verification is folded into set-password (the old `/verify`
gated on a cookie nothing set and always bounced to `/login`; it was removed). There is **no
`/signup`** — `validateSignup`/`signupErrorMessage` remain in `@graphene/auth` for a future invite
flow but have no caller.

## MFA (TOTP)

Three entry points, all on the same WorkOS primitives (`enrollAuthFactor`, `mfa.challengeFactor`,
`authenticateWithTotp`):

- **Opt-in** at `/account/security`: enrol (QR) → challenge → verify. Disabling re-enters the account
  password first (a correct password returns an `mfa_challenge`, so any non-`failed` outcome confirms it).
- **Challenge at login**: an enrolled user's `authenticateWithPassword` throws `rawData.code =
mfa_challenge` carrying a `pending_authentication_token` and the factors (but no challenge id). Login
  stashes the token + TOTP factor id in a short-lived `wos-mfa` cookie and redirects to `/mfa`, which
  mints a fresh challenge and exchanges the code.
- **Mandatory enrolment at login**: when the environment _requires_ MFA and the user has no factor,
  `authenticateWithPassword` throws `mfa_enrollment`; login stashes `wos-enroll` and redirects to
  `/mfa/enroll`, which both enrols the factor and completes login.

Three failure modes are handled so they don't masquerade as "wrong code": a **misconfigured sealing
secret** (`WORKOS_COOKIE_PASSWORD` < 32 chars → seal-time failure) surfaces as a 503, not a bad-code
error; a **spent/expired pending token** clears the cookie and redirects to `/login?expired=1`; and
anything that is **not a refusal of the submission** (`codeRefused` in `@graphene/auth` — a WorkOS
5xx, our own 429, a rejected API key, a request that got no answer) is a 503 "we couldn't check
your code", with the challenge cookie kept so the same form can be retried. Only a 4xx about the
code or token is reported as the user's mistake.

Once `authenticateWithTotp` has accepted the code the **pending token is consumed**: if the portal
check that follows (`validatePortalSession`) cannot confirm access, the new WorkOS session is
revoked and the challenge cookie is cleared, and a second submit of the same form goes back to
`/login`. The log signature of that is `Invalid one-time code` (the replayed code) followed by
`The pending_authentication_token has expired or is invalid` — it is a portal/API problem, not the
user's authenticator.

## Set-password, invites and forgot-password

Provisioning (admin or firm invite) creates the WorkOS user, then mints a WorkOS Invitation linked
to the firm's organisation and the selected WorkOS role. It emails a link to
our own `/set-password?invitation_token=…` page via Resend, dressed in **the portal's own brand and
sender identity** (`resolve_sender`). The organisation link is load-bearing: WorkOS supports both
new and existing Users on an organisation invitation, while the application-only invitation this
path previously issued could answer "User already exists" and silently turn a Platform invite into
a password reset. The reset-token path remains only as a compatibility fallback if WorkOS returns
that explicit existing-user refusal. The invitation is minted before the direct membership
reconcile so WorkOS does not reject the recipient as already added. Invitation lifetime is
`INVITATION_EXPIRES_DAYS` (1–30, default 7). The invitation is the emailed credential; only after
the recipient submits a password does the page mint and immediately consume the short-lived
password-reset credential, then accept the invitation. Members stay `pending`/disabled in the
mirror until that completes. Resending uses WorkOS's resend operation while the stored invitation
is pending, preserving its revocable invitation token; legacy rows without one use the
password-reset fallback. Team and Profiles can also revoke a pending invitation without removing
the portal grant.

### Why a password was refused, and how strong it looks

**The refusal is read off the EXCEPTION INSTANCE, not `rawData`.** A weak password is a **422**, and
the SDK's `UnprocessableEntityException` carries `code`/`message` on itself — it has no `rawData` at
all (only `GenericServerException`, `AuthenticationException` and `OauthException` do). Both password
paths used to read `e.rawData` alone, so nothing matched and every rejected password on
`/set-password` fell through to "Could not set your password. Please try again." The `'weak'` branch
was unreachable in production and `WEAK_PASSWORD_MESSAGE` had never been shown there. The suite was
green throughout, because every double threw `{ rawData: { code: 'password_too_weak' } }` — a shape
the SDK cannot produce for this error.

**The two exception classes disagree about `errors[]`, and that decides the design.**
`BadRequestException` (400) keeps the array; `UnprocessableEntityException` (422) never stores it —
it folds the codes into the message and throws the array away:

```
The following requirement must be met:
    password_too_weak
```

So on the one status a rejected password actually uses, the reason survives **only inside the
message string**. `pkgs/auth/src/password-errors.ts` therefore searches codes AND message text, and
**maps** the code to copy rather than echoing the message — echoing would print machine codes into a
destructive alert, which is what the old "pass the provider's message through" intent would have
done. AuthKit names six failures (`PasswordTooWeak`, `PasswordTooShort`, `PasswordTooLong`,
`PasswordPwned`, `PasswordContainsEmail`, `PasswordMissingCharacterType`); matching is on a
substring of the distinguishing word, because the exact strings are the vendor's, arrive only in a
live response body, and an exact list is one that goes stale silently. An unrecognised password
rejection still answers, generically — a seventh vendor code must read as a refusal, never a 500.

**The link beats the password.** `password_reset_token_expired` names both subjects, so the link
check runs first: telling somebody their password is weak when the link has expired sends them off
changing the one thing that was fine. Both branches were broken the same way, since a 400 has no
`rawData` either.

**One classifier, both surfaces.** `/set-password` and the three change-password forms share it.
Change-password used to stringify the error and flatten every reason onto `WEAK_PASSWORD_MESSAGE`,
so a password refused for being too LONG was reported as appearing in a breach.

**The strength meter is informative and never a gate** (`@graphene/ui`'s `PasswordStrength`). It
scores with **zxcvbn-ts — the library AuthKit itself uses**, so the bar and the verdict mostly agree;
a home-grown composition meter would call `Passw0rd!` strong and be contradicted by the server, which
is worse than no meter. It cannot be authoritative in any case: `PasswordPwned` and
`PasswordContainsEmail` are facts about the world, not about the characters, so a password can read
Strong and still be refused — `zaq1@WSXcde3` scores 4 and has leaked 11,741 times.

**FOUR segments, matching the scale WorkOS presents.** zxcvbn produces five scores (0–4) but the
dashboard's **Minimum password complexity** picker offers 1 · 2 · 3 · 4 — it cannot require 0 — so
the lit count IS the number an administrator sets: "needs 3" and "three segments" are one statement.
Scores 0 and 1 fold onto the bottom rung, sharing a word as well as a fill, so no step of the bar
means two different things. Very Weak · Weak · Good · Strong, named BELOW the bar and left-aligned:
beside it the word stole width the segments needed and made a full bar look unfinished, and a single
sliding fill stopped short of its track at the best score, reading as "not good enough" on a password
as strong as the scale goes.

**The colour is derived from the threshold, never hard-coded per tier** — green where the score is
accepted, amber one step short, red below. So a project set to require 4 gets a correct bar with no
edit, and the colour can never tell somebody a password is fine on a step the server refuses. The
tier line says so in words too ("Good — not strong enough yet"), because colour alone asks the
reader to know what amber means.

**THE POLICY IS A HAND-KEPT MIRROR: WorkOS exposes no API for it.** There is no password-policy
endpoint and nothing in the Node SDK — `/user_management/password_policy`, `/environment`,
`/environments/current` and `/user_management/settings` all 404 against the live API, and every
password identifier the SDK exports is authenticate/reset/update. So `MIN_PASSWORD_LENGTH`
(`@graphene/auth`) and `MIN_ACCEPTED_SCORE` (`@graphene/ui`) mirror the dashboard by hand, each with
a test stating the expected value so a change is a deliberate edit rather than a number that quietly
stops matching. They cannot share a home: the meter runs in the browser and `@graphene/auth` is kept
out of the client bundle. Both are overridable — it is project-per-tenant configuration, so a firm's
own project may differ.

The meter sits **after both
fields**, not between them: the password and its confirmation are one act, and a bar appearing
mid-form pushed the second field down as you typed. The dictionaries are the bulk of the library and load
on the **first keystroke**, not on page load — these are cold-start pages reached from an email — and
until they arrive the row renders nothing rather than a wrong number. `checkAsync` is deliberately
unused: its async matchers call out to a network, and a half-typed password must never leave the
page. The breach question is the server's to ask, at submit.

**`minlength` is on the set-password inputs now.** The three change-password forms have had it since
they were built; this page made you submit to discover a floor it already knew.

**A `pending` WorkOS membership is not a suspended one, and the vendor refuses to move it.** WorkOS
gives a membership three states — `pending` (invited, not accepted), `active`, `inactive` — and
answers 400 for both `reactivate` on a pending membership ("the invite must be accepted instead")
and `deactivate` on one ("it should be deleted instead"). Every one of those refusals is tolerated
rather than propagated (`provisioning._pending_membership_refusal`), because **WorkOS membership
state is not what grants portal access — the `auth.organisation_memberships` mirror is**: there is
nothing to activate or deactivate at the vendor for someone who has not accepted, and refusing
would leave a person suspended while their invitation was outstanding permanently un-restorable.
Untolerated it cost more than a suspend: `grant_portal_access` reactivates a membership the create
answered `inactive` for, so an invitation issued in between turned the refusal into a **failed
invite** — no email, no mirror row, and the vendor's wording shown to a firm inviting a client for
the first time.

**`create_organization_membership` is an upsert and therefore also reactivates, so it carries the
same refusal** — against a `pending` membership it answers 400 rather than echoing the membership
back, and pending is the ORDINARY state of a re-invite because the invitation minted moments
earlier is what makes it so. That is the half the first fix missed, and the order is what made it
expensive: by the time the create runs, the person's previous invitation has been revoked and
replaced, so the failure destroyed a working link and produced nothing, every retry doing it again.
Its signature in the WorkOS event log is `invitation.revoked` + `invitation.created` pairs with **no
`organization_membership.*` event between them** — the membership step never landed. It is
recovered exactly as a conflict is (the membership exists; the list says which), and the role
update is tolerated too, since the invitation carries the same `role_slug` anyway. The test double
had modelled the upsert as a plain echo, which is why the suite was green for a vendor contract
that does not exist.

The corollary in the UI: **Deactivate/Reactivate is decided by the membership's status, never by
`auth.users.enabled`** — one shared rule in `@graphene/ui`
(`member-directory/access-state.ts::isSuspendedMember`, read by the platform portal's Team and
Profiles listings and records and by the admin portal's tenant members and operations staff, since
four menus reading four signals is how two of them came to offer an act the vendor can only
refuse). `enabled` is set from
WorkOS's `email_verified`, so it is false for every outstanding invite — which had a client invited
seconds earlier rendering as "Reactivate", the one state control on the row and one the vendor
could only refuse. What a pending person needs is Resend or Revoke invitation, which those menus
already offer off the same status.

`/forgot-password` and login's email-not-confirmed branch both call the API's
`POST /internal/forgot-password`, which:

- **never reveals whether the account exists** — a fixed `{ok: true}` on every branch (throttled,
  suspended firm, unknown user, send failure);
- is **throttled** per email, per portal host and (when the portal passes it) per end-user IP;
- **refuses a suspended firm** — minting a set-password link for a locked-out firm's member would be
  a way back in, so it silently sends nothing.

The **non-production send guard** (`EMAIL_ALLOWLIST`, inside `app/email/transport.py::send_email`) means that
when `APP_STAGE` is `dev` or `staging` a recipient is mailed only if it matches the allowlist. The
allowlist defaults to `@grapheneplatforms.com`, so those stages mail Graphene addresses and nobody
else. An explicit blank allowlist suppresses every send there. `EMAIL_NONPROD_GUARD=false` turns the
guard off. Every other stage, including production, does not consult the list.

## User management

Firm staff with `members:read` can open a tenant-scoped auth profile from **Team** or **Profiles**.
The profile combines the identity mirror, complete portal role sets and effective permission union,
client-login links (`crm.contact_users`) or assigned staff book (`crm.client_advisers`), live WorkOS
MFA/session posture, last sign-in, and the most recent authentication/mutation events. A client
login with no linked client is called out explicitly: it can authenticate, but its RLS scope resolves
to nothing.

`members:write` additionally permits password-reset email, MFA-factor reset, deactivate/reactivate,
ending one live session, and the existing role/invite actions. Password and MFA recovery both
revoke live WorkOS sessions; MFA reset deletes every factor and therefore requires the operator
to verify the caller first. A session revoke checks the id is on that person's live list before
calling WorkOS — a bare session id is not enough, because WorkOS revokes by id alone.
Every password, MFA and MFA-enrolment completion checks `/me/portal` before writing its session
cookie. That answer is the portal already resolved for the request, including the portal the
caller named, so the check does not classify the token a second time and drop the name.
WorkOS can authenticate an identity whose organisation membership has been deactivated;
the app-owned active membership is therefore the final, fail-closed sign-in gate, and a refused
new WorkOS session is revoked.
Graphene's own staff use the same profile from **Internal Operations**, gated by
`operations:read`/`operations:write`.

Live sessions are copied into `auth.sessions` (0214). Two writers, neither of them on the
page: the worker's WorkOS events poller applies `session.created` and `session.revoked`
(the payload is the session — IP, user agent, status, expiry), and opening a person's
profile, or your own security page, lists that person's live sessions from WorkOS after
the response has been sent. The page itself only reads the table. A sign-in updates the
table when the worker is consuming the stream; until that copy or the background list has
run, the table is empty. The cursor does not replay events from before it, so the poller
also lists each person once (`users.sessions_synced_at`). A revoke moves that timestamp,
and a list that was already in flight is dropped rather than writing the revoked session
back. WorkOS sends no expiry event; a row past `expires_at` drops out of the read.

The row marked "this browser" is still filled from the request viewing the page. If that
session is not in the mirror yet, the read adds a blank current row in memory so the browser you
are in is named; it is not written. The UI labels a device from the user agent ("Chrome on
macOS"). "This browser" is the access token's `sid`. Ending the current browser is refused.

Ending any other session still calls WorkOS at that moment, which is what signs the browser out.
The local row is removed in the same request when this app revoked it, and by `session.revoked`
otherwise. Changing a password, or signing out everywhere else, still enumerates WorkOS — a
session the mirror has not seen must not stay signed in — and then asks `POST /me/sessions/refresh`
to copy the result back. What is stored on `authentication.succeeded` is unchanged: IP, a truncated
user agent, and that device label, so the history survives after the session ends. The raw values
are not logged.

Ordinary authenticated GET requests are deliberately not action events. The profile's event list
shows sign-ins and successful actions from `audit.actions`, and the latest page views beside
them. Page views are a separate stream, `audit.page_views` (0213). A full document load is posted
by the three portals' `handle` hooks, after the response, without waiting. A client-side move is
posted when it commits to a different pathname and the page status is 2xx (`afterNavigate` also
fires for an error page, and that is not a view). A hover preload is the same data request as
that move, so the server does not count data requests at all. A subrequest, an invalidation
refetch, and a form that stays on the page are not views. The heatmap on the same section is
the last 53 weeks of those views and of the action stream, by London day. On a firm profile the
path list omits a client, account, or compliance path unless the viewer holds the grant that
would let them open it. A document id is omitted wherever it sits in the path, including under
a client, unless the viewer can also read documents. The counts stay.

## The API: Principal, verification, and the environment registry

`app/auth.py`:

- `get_current_principal` verifies the bearer JWT and returns a `Principal`
  (`user_id`/`organisation_id`/`role`/`permissions`/`session_id`/`portal`). `portal` is the
  same answer `GET /me/portal` used to be a second round-trip for — `auth.resolve_claims`
  already ran inside `_permissions_for`. It also stashes the principal on
  `request.state` so the action-events middleware can attribute the request after the response.
- **Multi-environment verification.** `tenant_registry` holds an in-memory `issuer → env` and
  `org_id → env` map, loaded from `auth.tenant_environments` at startup and **reloaded on a
  miss** (`tenant_registry.resolve`): a token whose org/issuer is unknown re-reads the table
  before falling back, at most once per `MISS_RELOAD_INTERVAL_SECONDS` and single-flight. That
  is what keeps every replica current — the map is per process, there is one API per site, and
  `POST /admin/environments/refresh` reaches only the replica that served it. Production ran
  that way for a day: Select was registered after the three APIs had booted, Gravelines was
  refreshed, London and Frankfurt were not, and a correct password and TOTP code worked on one
  site and 401'd (`Unable to find a signing key`) on the other two until the containers were
  restarted. An unrecognised token that survives the reload falls back to the single configured
  environment (`WORKOS_CLIENT_ID`) — safe because the per-project JWKS signature check is the
  gate. The fallback fails **closed** (a real tenant token fails the core-project signature → 401).
- **Authority is the `system` permission only.** `is_system(principal)` is true iff the verified
  token carries the `system` permission. The token's `role` is a **free-text name chosen inside a
  tenant's own WorkOS project and carries no privilege** — it is namespaced by `role_claim()` before
  being published to RLS (defence-in-depth), and neither Python nor SQL grants on it (see the
  `is_system` note under [Authorisation](#authorisation-permissions-in-the-token)).
- `require_permission` / `require_any_permission` gate the `/firm` and `/admin` surfaces: `system`
  bypasses, and **`:write` satisfies the matching `:read`** (write implies read).

## Session dependencies and the RLS claims contract

The request path connects as the DML-only `app` login role. A session dependency establishes the
per-request DB context. There are eight, in three families (usage counts approximate, Aug 2026):

**RLS (authenticated) path — the read hot path**

- `tenant_session` (~117 uses) — `SET LOCAL ROLE authenticated`, then `auth.resolve_claims(user, org)`
  (SECURITY DEFINER) mints the pre-resolved internal claims (`user_uuid`/`tenant_id`/`tenant_ids`/
  `contact_ids`/`account_ids`), merged with the JWT-derived half (`sub`/`role`/`permissions`/`sid`)
  and published as `request.jwt.claims` (transaction-scoped — correct under PgBouncer transaction
  pooling). **Fails closed** (403) when the identity/org doesn't resolve to a known user + tenant, and
  refuses a **suspended** firm (`_refuse_if_suspended`, run _after_ claims are published because
  `auth.tenants` is claim-scoped under RLS).

**Service-role paths — writes to `auth.*` / cross-tenant work**

- `firm_session` (~101) — firm self-service (the platform portal managing its own people/roles/etc.).
  Runs as `service_role`; the caller's tenant is resolved from _their_ verified identity and every
  query is forced to that `tenant_id`. Permission-gated per endpoint; suspension-checked.
- `admin_session` (~112) — the Graphene Internal Operations surface. `service_role` (BYPASSRLS).
  **Coarse gate**: the caller must be an operations principal (`is_system`, or resolved to the
  operations tenant). **Granular RBAC** per endpoint (`tenants:*`/`operations:*`/`model:*`/
  `platforms:*`/`aml:*`/`audit:read`; `system` bypasses). Deliberately _not_ suspension-checked (ops
  must be able to act on a suspended firm).
- `system_transaction` / `get_session` — non-request background/system work (e.g. the registry load).

**The portal waterfall — Graphene operations acting on a firm's book (`?tenant=<uid>`)**

- `book_session` (~26), `firm_book_session` (~3), `firm_book_write_session` (~6). Without the param
  each _is_ its base dependency (byte-identical to the firm/client path). With `?tenant=<uid>` — an
  **operations-only** param — an operations principal holding `book:read` (reads) or `book:write`
  (proxy writes) acts on the target firm's book with firm-staff-equivalent visibility. The acting
  operator's `sub` stays their own (audit names the real person); RLS, not the handler, remains the
  gate. See `app/db.py` for the visibility overlays and the read/write split.
- **All three now defer to the tenant workspace for an operator who holds it** (`tenant_portal:read`
  for the reads, `tenant_portal:write` for the proxy writes → `target_*_session`). The two families
  front the SAME handlers — a client's bank details, an account's model mandate, the book listings,
  the document/task/flow listings — so refusing them for want of the separate `book:*` grant is what
  made the workspace's own tabs 403. `book:*` keeps working unchanged, and a `system` principal
  keeps the book proxy's own path, where the operations-scope check has always been skippable for it.

**The complete tenant workspace — Configure and Operate are separate authorities**

- `target_tenant_session` and `target_firm_session` are the generic target-aware counterparts of
  `tenant_session` and `firm_session`. With no `tenant` query they delegate to the existing firm
  path. With `?tenant=<uid>` they require a principal resolved through the **Operations
  application** into the operations tenant, then require `tenant_portal:read` for safe methods or
  `tenant_portal:write` for mutations. A `system` grant does not turn a Platform-application
  session into an Operations session.
- The target claims replace only the tenant scopes (`tenant_id`, lineage, contact/account scopes
  and target role ids). The operator's `sub` and `user_uuid` remain their own. RLS reads therefore
  see the selected firm while database audit triggers and `audit.actions` still name the real
  Graphene operator.
- **The borrowed permission vocabulary is as wide as the request was authorised, not as wide as the
  firm.** A safe method borrows the target firm's `:read` slugs (plus `documents:write`, which
  `GET /documents/clients` gates on); a mutating one borrows the whole platform set. The split is
  load-bearing because the overlay is republished INTO `request.jwt.claims`: granting the write
  vocabulary on a GET would hand a read-only operator claims that satisfy the firm's write policies.
- **A uid resolver must declare the same session as the endpoints that use it** (`app/addressing.py`).
  FastAPI caches a dependency per request, so agreeing means one session and one scoping;
  disagreeing opens a second session bound to the OPERATOR's tenant, the uid resolves against the
  wrong book, and the workspace answers 404 for a record that is plainly there. The resolvers take
  the target-aware sessions (which delegate verbatim with no target named) and
  `test_target_tenant_context.py::test_uid_resolvers_ride_their_endpoints_own_session` asserts the
  pairing across every route.
- **Firm ADMINISTRATION is not proxied through the firm's own door.** The directory READS
  (`GET /firm/members`, `/firm/members/by-uid/{uid}`, `/firm/roles`) are target-aware, because the
  workspace's task, journey and client-people surfaces pick a person or a role from the firm's own
  list. Inviting a person, editing a role and setting branding stay on `firm_session`: Graphene does
  those through `/admin/tenants/{id}/…` under `tenants:write`, and a second route to the same power
  reachable with `tenant_portal:write` alone is not wanted.
- `target_tenant_read_session` is the explicit exception for POSTs that mint an ephemeral read
  channel. Messaging realtime tickets use it, so a read-only operator can subscribe to the
  selected firm's feed without gaining ordinary POST authority.
- A suspended firm remains targetable from Operations. Suspension blocks the firm's own sessions,
  not the support surface needed to diagnose or reverse the suspension.
- `tenant_portal:*` means **Operate**, not tenant administration. Existing `tenants:*`,
  `platforms:*`, `audit:read` and `book:*` permissions still gate Configure, catalogue, regulated
  evidence and legacy book surfaces independently. The only non-tenant capabilities omitted from
  Operate are the signed-in person's own account, authentication/security, notification
  preferences and storage credentials.

## Authorisation: permissions in the token

- **Permissions travel in the verified token** (`permissions` claim) and are read by both Python
  (`require_permission`) and SQL (`auth.jwt_permissions()`), so the RLS hot path does **zero RBAC
  queries**. WorkOS is the system of record; the `auth.*` RBAC mirror is a read cache written only by
  the sync/provisioning path.
- **The registry is the single source of truth.** `app/permissions.py` declares every permission once
  (`resource:action`, with a `portal` attribute of `client`/`platform`/`operations`); `sync_permissions()`
  upserts them into `auth.permissions` on startup, and `scripts/generate_permissions_ts.py` emits the
  typed TS constants the portals import. Taxonomy: every resource has `read` + `write` (write ⇒ read);
  big features add named actions (`contacts:read_all`, `documents:send`, …). **The 266
  `require_permission` call sites pass `Permission` constants, not raw slugs** — so a slug change is a
  one-line registry edit, not a call-site sweep.
- **`system` is the only superuser signal, in both languages.** `auth.is_system()` in SQL reads the
  `system` permission only — the historic `role IN ('system','admin')` branch was **removed in
  migration `0034_is_system_permission_only`** (so a firm naming a role "admin" no longer inherits
  cross-tenant access). A Graphene role may still _carry_ `system` (the operations tenant is
  allowed to grant it) and be attached to a platform membership as well; `_permissions_for`
  drops those slugs unless the resolved portal is operations, so a platform-portal session
  honours the firm toggles. The `role_claim()` namespacing in `app/auth.py` is now
  belt-and-braces for _other_ claims minters (a psql session, a future service, a direct
  `set_config`).
- **RLS uses pre-resolved claims.** `auth.tenant_lineage()` gives staff their firm + descendants;
  a **client-portal session gets an empty lineage** (the `auth.role() = 'client'` DENY lever), so a
  client is reachable only via the explicit `contact_scope()`/`account_scope()` policies, which
  **fail closed** (absent claim ⇒ empty scope). `contacts:read_all` is the tenant-grantable "see the
  whole firm's book vs my own" entitlement.

## The dev bypass

- **There is no break-glass.** The `SYSTEM_USER_IDS` allowlist is deleted: the ONLY way to be a
  system principal is an operations-portal session whose role carries the `system` permission,
  read from the DB on every request. A Graphene platform session does not inherit it from the
  same role (`permissions.for_portal`). Bootstrapping a fresh environment grants that role via
  `pnpm db:bootstrap` (`moon run api:bootstrap-dev` — `scripts/bootstrap_dev_tenant.py` then
  `scripts/bootstrap_dev_environment.py`), never via an env allowlist. Re-run after
  `pnpm db:reset`, or when local memberships have been left `inactive` (login then refuses with
  no portal access even though WorkOS still authenticates). After a reset the events poller
  must seed its cursor at the _tail_ of the WorkOS stream — `order=desc` is ignored on a typed
  Events query, so a `limit=1` seed was the oldest event and the next tick replayed history,
  including membership deletes that the backfill had already superseded.

The Events poller is a worker loop, enabled every 60 seconds by default in every deployed
environment (`WORKOS_SYNC_POLL_SECONDS`). It uses WorkOS's native `AsyncWorkOSClient`, so a slow
Events or membership request yields the worker loop rather than freezing uvicorn; the API process
does not host the reconcile. A transaction-scoped advisory lock per tenant environment preserves
single-writer cursor semantics across overlapping worker generations or future worker replicas.
Setting the interval to `0` deliberately pauses automatic mirror reconciliation and is an
operational override, not the staging configuration.

- **The `dev:` bearer bypass** (`app/auth.py`, mirrored by a `dev-auth` cookie in the client and
  platform portals' `hooks.server.ts`) accepts an unverified principal for E2E/dev. It is
  quad-guarded — `DEMO_AUTH_BYPASS=1` **and** `APP_STAGE` in `{dev,local,test}` **and** a loopback
  peer **and** an unproxied request (no `X-Forwarded-*`/`X-Real-IP`/`Forwarded`, loopback `Host`) —
  and defaults **off** (`APP_STAGE` defaults to `production`). The portal side is one
  implementation, `@graphene/auth`'s `devPrincipal` (`pkgs/auth/src/dev-bypass.ts`, tested), so the
  four guards cannot drift between the API and the hooks: the hooks used to check only the flag
  and "stage is not the literal `production`", which a `staging` deploy with the flag left on
  would have passed. Both portals' logout actions also clear the `dev-auth` cookie — it beats the
  sealed session in the hook, so a sign-out that cleared only the session did not sign you out.
  The admin portal has no dev bypass.

## Environment & configuration

Each portal (`apps/{client,platform,admin}/.env`) needs the WorkOS env for the **fallback/local**
environment; per-firm keys live encrypted in `auth.environment_secrets` (Fernet, `SECRETS_MASTER_KEY`):

```
WORKOS_API_KEY=sk_...
WORKOS_CLIENT_ID=client_...
WORKOS_COOKIE_PASSWORD=<32+ char secret>   # openssl rand -base64 32
API_URL=http://127.0.0.1:8000
INTERNAL_API_TOKEN=<shared secret for the portal↔API internal endpoints>
```

API (`apps/api/.env.local`): at least `WORKOS_CLIENT_ID` (JWKS for the fallback env),
`SECRETS_MASTER_KEY` (decrypt per-firm secrets), `INTERNAL_API_TOKEN`, and the Resend/allowlist config.
Optionally pin the fallback issuer with `WORKOS_ISSUER`. Never commit real `.env` files.

Local login after a reset: `pnpm db:bootstrap`. The tenant script restores `mark@grapheneplatforms.com`
and `gio@grapheneplatforms.com` as staff with active platform + operations memberships; the environment script registers
`localhost:5173` / `:5175` / `:5174` against the WorkOS project in `apps/api/.env` and needs
`DEV_{CLIENT_PORTAL,PLATFORM,OPERATIONS}_APP_ID` / `_SECRET` plus `WORKOS_COOKIE_PASSWORD` (≥32
chars, matching the portals). Branding is optional (`scripts/bootstrap_dev_branding.py`).

WorkOS dashboard, per firm project: enable **email + password**; set a **sign-out redirect**;
**disable WorkOS's own emails** (we send via Resend); set the auth policy to _MFA optional_ or
_MFA required_ (both are handled at login).

## Key files

- `pkgs/auth/src/` — `auth.ts` (`passwordAuth`), `session.ts` (`resolveSession` refresh dance),
  `sessions.ts` (live list, `revokeOwnedSession`, `revokeOtherSessions`), `client-context.ts`
  (IP, user agent, device label), `page-view.ts` (document loads, plus the committed-navigation beacon),
  `trace.ts` (the W3C trace id a page render stamps on its API calls; the async-local storage
  is imported only when a server request starts, so it never loads in the browser), `portal-header.ts`,
  `errors.ts` (WorkOS error classifiers), `cookies.ts`, `return-to.ts` (open-redirect-safe
  `?next=`), `environment.ts` (`consumeRateLimit`, `mfaChallengeHandle`, `safeClientAddress`),
  `gates.ts` (the hook's terms / wrong-portal decision), `csrf.ts`, `relay.ts`, `dev-bypass.ts`,
  `validation.ts` — each described in [`packages.md`](packages.md).
- `apps/*/src/lib/server/{workos,auth,validation}.ts`, `src/hooks.server.ts`,
  `src/routes/{login,mfa,mfa/enroll,set-password,forgot-password,logout,account/security}/`.
- `apps/api/app/auth.py` (verify + `Principal` + `is_system`/`require_permission`), `app/db.py`
  (the session dependencies), `app/tenant_registry.py`, `app/environments.py`, `app/provisioning.py`,
  `app/permissions.py`, `app/routers/internal.py` (forgot-password, rate-limit).
- `apps/api/migrations/sql/0001_auth_core.sql` (schema + RLS helpers + audit spine),
  `0030_auth_directory_scope.sql`, `0034_is_system_permission_only.sql`.
- Tests: `apps/api/tests/test_auth.py`, `test_db.py`, `test_rls*.py`; `pkgs/auth/src/*.test.ts`.

## Known gaps

The org-per-portal model and every auth fallback were deleted in the app-only cutover
(migrations 0107/0108; see `docs/auth-audit.md` for the audit that drove it). What remains, tracked:

1. **The dev `dev:` bypass remains** (quad-guarded, off by default) — it is what the E2E suite
   authenticates with. Replacing it needs a real E2E auth strategy first.
2. **The rate limiter is per-process, in-memory.** Login, MFA, forgot-password, postcoder and search
   throttles are multiplied by replica count and reset on deploy; a shared store (Redis) is needed
   for a real cap. Login and MFA also fail _open_ if `/internal/rate-limit` is unreachable
   (deliberate, but soft). The MFA cap (Sept 2026) is per pending challenge — the portal keys it on
   a SHA-256 of the pending-auth token, so the token itself is never a limiter key — and per IP; a
   refused challenge clears the MFA cookie and sends the user back to login for a fresh code, which
   is the one thing a guesser cannot do without the password.
3. **Auth route actions are copy-pasted across the three apps.** A fix must land in three files.
   Consider a shared route module.
4. **Session cookie lifetime is ~400 days** (`cookies.ts`), longer than the effective auth life.
5. **`operations_host` is not covered by the 0099 host-uniqueness indexes**, and 0099 soft-fails on
   pre-existing duplicates — verify the indexes exist on any long-lived DB.
6. **Sweep gaps**: completing `/set-password` and `/internal/forgot-password` do not sweep other
   live sessions (admin/firm-initiated mints do). The pre-cookie portal-access check revokes a new
   session it refuses; the layout guard remains defence in depth for an older cookie.
