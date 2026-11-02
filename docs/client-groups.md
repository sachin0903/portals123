# Client groups

A **group** is a tenant-owned tag a firm puts on a client. The usual reason is a
business stream — Workplace, Charity, a named proposition — so that stream can
see its own client-portal design.

It is not a [book](client-classifications.md). A book is one label, mostly
derived from the accounts, and it also decides whether the client may move
money. A client wears one book. A client may wear several groups, and a group
grants nothing.

It is not a free-form contact tag (`crm.contact_tags`, [`flows.md`](flows.md)).
Those are automation labels: no catalogue, no stable slug, and a client cannot
read them. A group is a catalogue the firm names, with a slug a design can bind
to.

## Resolve order

For a signed-in client the portal hook reads `GET /crm/classifications/mine` and
`GET /crm/groups/mine` together, then
`resolveClientPortalTemplate(branding, { groups, classification, role })`. The
group list is already in catalogue order (`sort_order`, then id).

1. **First group with a design** — walk the client's groups. The first slug that
   appears on a template's `group_slugs` wins.
2. **Book** — the resolved classification slug, as today.
3. **Role** — leftover `role_slugs`.
4. **Default** — `default_template_id`.

A group with no design is skipped, so tagging someone "VIP" does not hide the
book's shopfront. A firm that never creates a group sees no change.

One slug belongs to one design. Assigning it to another design removes it from
the first, the same rule as books. Changing which groups a design serves is a
composition change (`pages:write`).

Money movement stays on the book. A workplace group does not turn self-serve
payments on.

## Catalogue

`crm.client_groups`: `name` (≤40), `slug` (`^[a-z0-9-]{1,32}$`, unique per
tenant), `description` (≤160), `sort_order`. Lower sort order wins when a client
is in more than one group that has a design. Clients may read the catalogue;
they cannot write it.

Membership is `crm.contact_groups`, replaced as a set
(`PUT /crm/contacts/{uid}/groups`, `contacts:write`, at most 20). A client reads
only their own rows. Deleting a group or a contact drops the membership. A
reset keeps the catalogue and clears the memberships with the contacts.

Renaming a slug detaches any design that was bound to the old one until the
firm assigns it again.

## Surfaces

- **Studio** (`/branding`) — catalogue CRUD (`branding:write`) and the design
  each group uses (`pages:write` to change it).
- **Client record** — a badge per group, and a menu to add or remove
  (`contacts:write`).
- **Client portal hook** — `GET /crm/groups/mine` beside the book, before SSR
  head. `contact_id` is the active-contact cookie, refused when the session
  does not hold it.

## Audit

Catalogue edits are named for the group (`client_group.created` / `.updated` /
`.deleted`). Replacing a person's groups is named for the client
(`client.groups_set`), with the uids on the row.
