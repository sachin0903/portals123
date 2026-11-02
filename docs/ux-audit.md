# UI/UX audit — platform (adviser CRM) + client portal

**Bar:** we sell to billion-dollar clients and compete with intelliflo office (iO) on
client management. The UI must read as a premium, trustworthy financial CRM. Today it does
not. This is the brutal version, then a phased plan.

## Brutal findings

1. **The platform Dashboard is fake.** `128 clients / £84.2m / "Margaret Cole"` are
   hardcoded in `apps/platform/src/routes/+page.svelte` — while real, RLS-scoped data sits
   one click away in Clients. A prospect who lands on a fabricated home page stops trusting
   everything else. **Credibility-critical.**
2. **Numbers aren't trustworthy.** Returns render as `+1,287%` because several holdings have
   near-zero `book_cost` in the source and we divide blindly. At this calibre an obviously
   wrong number is worse than no number.
3. **The CRM is grey tables + native `<select>`s.** The Clients list has no search, sort,
   segmentation, avatars, AUM column, last-activity or next-review — the table stakes of any
   adviser CRM. iO leads with exactly these. Native unstyled dropdowns cheapen every form.
4. **The client record is a flat stack of equal-weight cards** (Details / Accounts / Advisers
   / Logins). No identity header, no at-a-glance KPIs (AUM, # accounts, service, client
   since, next review), no tabs. Everything shouts equally, so nothing reads.
5. **No CRM-lite substance.** `crm.contact_notes` exists but is never surfaced. No notes,
   tasks, relationships, tags/segments, or activity timeline. iO is fundamentally a
   client-management system; we're a read-only viewer.
6. **Inconsistent chrome.** Some pages use `PageHeader`, others hand-roll `<h1>`; native
   selects vs `Select`; raw `text-emerald-600` instead of the `--positive` token. Small tells
   that read as unfinished.
7. **Thin states.** Empty/loading/error states are minimal or missing; no skeletons on
   data-backed pages.

## Terminology

Product language is **Client**, never "contact", for a person/entity we serve. The UI already
says "Clients" in the main flows; `crm.contacts` stays as the internal party table (Decision 8)
but nothing user-facing calls a person "a contact". ("Contact details" on account-settings
means contact _information_ — kept.)

## Plan (phased)

**Phase 1 — trust + the two screens a buyer sees first (this pass)**

- Shared: a `Stat` KPI tile, an initials `ClientAvatar`, and `formatReturn()` that refuses to
  show an implausible percentage (returns `null` → "—") — used everywhere money renders.
- Real platform **Dashboard**: firm KPIs (clients, AUM, accounts) + an attention list, from a
  new RLS-scoped `/crm/overview`.
- **Clients list** as a real CRM index: toolbar (search, segment filter, sort), initials
  avatars, service/status pills, adviser, right-aligned AUM, result count, empty state, a
  primary "New client".
- **Client record** rebuilt: identity header + KPI row + tabs (Overview / Accounts / People /
  Notes), with **Notes** (read + add) surfacing `crm.contact_notes`.

**Phase 2 — CRM-lite depth**

- Activity timeline on the record (transactions + notes + system events).
- Tasks / next actions + "review due" flagging and a dashboard queue.
- Tags / segments; saved filters on the list.
- Relationships surfaced (spouse, trustees, connected entities via `crm.relationships`).

**Phase 3 — polish + consistency**

- Replace all native selects with `Select`; unify page chrome on `PageHeader`; tokenise
  colours; add skeletons/empty/error states everywhere; keyboard + a11y pass; density toggle.
- Client portal: performance chart real range control, allocation legend, statements.

Success = an adviser demo where every number is real and defensible, the Clients list feels
like a CRM, and the record page answers "who is this client, what do they hold, what's
outstanding" in one glance.
