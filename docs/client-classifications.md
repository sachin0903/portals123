# Client classifications

A **classification** is a tenant-owned book label. It answers two questions the login cannot:
which client-portal design this person sees, and whether they may add or withdraw money
themselves.

WorkOS roles are the wrong grain. Auth profiles decide _who may sign in_. Advised vs D2C is a
**book fact**, and both books of one firm live in **one tenant**. A person can also be advised
on one account and execution-only on another — service already lives at account level
([`docs/client-record.md`](client-record.md)). The classification is the firm's rule for which
shopfront that mixed book gets, unless a staff member pins one.

The motivating case: a firm with an advised book (read-only portal) and a D2C low-value book
(self-serve money), both on this platform, both in one tenant.

A business stream is a different question. That is a [client group](client-groups.md): a tag
the firm assigns, several of which a client may wear, used to pick a portal design ahead of
this book. The book still decides money movement.

## Resolve order

For a signed-in client the portal hook calls `GET /crm/classifications/mine` and then
`resolveClientPortalTemplate(branding, { classification, role })`. A login linked to more than
one contact passes `contact_id` — the active-contact cookie — so the book is the person on
screen, not always the login's own `self` contact. An id outside the session's contact scope
is refused. With no `contact_id` the book is still the login's own.

1. **Pinned** — `crm.contacts.classification_id` is set. That book wins.
2. **First matching rule** — catalogue rows that are not `is_default`, in `sort_order`.
3. **Default** — the one `is_default` row (at most one per tenant).
4. **Nothing** — branding falls back to `default_template_id`, then leftover `role_slugs`.

`role_slugs` on a design set remain as a fallback for firms that have not moved off
auth-profile assignment. A classification slug is preferred when both are present.

`/mine` returns the public view only: `uid`, `name`, `slug`, `capabilities`, `source`. Match
predicates stay off the client payload.

## Match predicates

An empty rule matches everyone — useful only as the default, or as a last-resort row the
firm puts last in `sort_order`. Otherwise every set predicate must pass:

| Predicate                   | Meaning                                                                                           |
| --------------------------- | ------------------------------------------------------------------------------------------------- |
| `contact_types`             | `individual` and/or `organisation`                                                                |
| `any_account_service_types` | the contact holds **any** open account in this set                                                |
| `account_service_types`     | **every** open account is in this set; if they hold none, `contacts.service_type` is the stand-in |
| `min_value` / `max_value`   | sum of open, non-closed account values. A missing value fails a bound                             |

Account service is the captured `investment_capacity`, else `discretionary` when a model is on
the account — the same expression the accounts listing uses. There is no
`platform.accounts.service_type` column. `advisory` normalises to `advised`.

Suggested pair for the advised / D2C firm:

- **Advised** (`sort_order` 0): `any_account_service_types: [advised, discretionary]`,
  `capabilities.money_movement: false`
- **D2C** (`sort_order` 10): `account_service_types: [execution_only]`, `max_value` at the
  firm's low-value cut, `capabilities.money_movement: true`
- **Standard** (`is_default`): empty match, read-only

## Capabilities

`capabilities.money_movement` is the UI (and future API) gate for client-initiated add /
withdraw on the book as a whole. The transfer routes 404 for a book without it **and** with no
execution-only account. Since Oct 2026 those routes are a real three-step journey over
`/crm/money` — the client's request becomes an ordinary `platform.instructions` row, with a
safety check that holds a large withdrawal for the firm
([`contributions.md` §10](contributions.md#10-client-raised-money-oct-2026)) — so the gate is
what stops an advised account being offered a form it cannot use.

An account's own service still wins on its own page. A person advised on one account and
execution-only on another keeps payment and withdrawal on the execution-only account
(`accountHeaderActions`), and the transfer page lists only those accounts when the book itself
cannot move money.

The same gate governs the **portfolio header's buttons** (Sept 2026). The block's `actions`
setting (`deposit_withdraw | contact | none`, [`portal-customisation.md`](portal-customisation.md))
is a firm's layout choice, and the default layouts ship `deposit_withdraw`; that put "Make a
payment" and "Withdraw" on an advised client's home hero, each linking to a `/transfer/*` route
that 404s for their book. `page-data.ts::gateHeaderActions` rewrites `deposit_withdraw` to
`none` for every block on the page unless the resolved classification says
`money_movement: true`; an explicit `none` or `contact` is left alone. It used to rewrite to
`contact` — a "Contact your adviser" button in the pair's place — but the shell's top bar carries
the message button on every page, and a second one on the hero read as a form the client was
being pushed towards, so since Oct 2026 the row simply goes; a firm that wants the hero button
sets `contact` itself. The studio still shows the firm the setting it chose — the gate is applied
at read time, per client, not written back.

Unknown capability keys are dropped at the service layer so this cannot become a second
settings dump.

## Surfaces

- **Studio** (`/branding`) — catalogue CRUD (`branding:write`). Each book row chooses its Portal
  design; design sets do not expose the old client-role assignment dropdown. Advisers may **read**
  the catalogue with `contacts:read` so they can pin.
- **Client record** — badge on the name. Staff with `contacts:write` pin or clear
  (`PUT /crm/contacts/{uid}/classification`). Clearing rematches.
- **Client portal hook** — `GET /crm/classifications/mine` after the session opens, before
  SSR head. Branding cache stays tenant-wide; this call is per client.

## Schema

Migration `0181_client_classifications`. `crm.client_classifications` is tenant-scoped with
a composite unique `(tenant_id, id)` so the contact pin is a composite FK (0035). Clients
may read the catalogue via `auth.tenant_id()`; they cannot write. One default per tenant
(partial unique). Deleting a book `SET NULL`s pins so the contact rematches.

Do not add classification onto `GET /crm/context` (frozen key set) or `GET /me` (identity).

## Audit

Catalogue edits are named for the book (`client_book.created` / `.updated` / `.deleted`); a pin is
named for the client (`client.book_pinned`), because it overrides the rules for one person and that
is the question a file review asks. The reset manifest treats the catalogue as **configuration** —
a reset keeps the books and clears the pins with the contacts.
