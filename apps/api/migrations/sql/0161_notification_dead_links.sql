-- Repair notification links that name no route, so an old notice stops 404ing when it is clicked.
--
-- The link is STORED ON THE ROW, not derived at render time, so correcting the catalogue fixes
-- only what is written from now on: every row already sent keeps pointing at nothing, for ever.
-- Three families, all observed on a real book:
--
--   /adviser/messages   the client portal names no such route. Already corrected in the catalogue
--                       ("`/messages`, not `/adviser/messages` -- the latter names no route at
--                       all"), so these rows predate that fix.
--   /account            the same mistake in `_CLIENT_DEFAULTS["contact.bank_details_changed"]`,
--                       corrected in the same change as this migration. The client portal has no
--                       bank-details view outside onboarding, so `/profile` is the closest honest
--                       destination -- which is what the catalogue now emits.
--   /clients/<serial>   a deep link minted before the estate moved to uid addressing (docs/api.md,
--   /accounts/<serial>  "Addressing": a portal URL never carries a database key). The serial is
--                       still resolvable, so these are rewritten to the record they meant rather
--                       than thrown away.
--
-- SET LOCAL ROLE service_role IS LOAD-BEARING, not tidiness. `notifications.notifications` is
-- FORCE ROW LEVEL SECURITY (0063), and the migrator is not BYPASSRLS -- so without it every
-- statement below sees the rows its (absent) request.jwt.claims allow, which is none, updates
-- nothing and COMMITS HAPPILY. CLAUDE.md records this costing the estate three separate incidents.
SET LOCAL ROLE service_role;

-- The two client-portal 404s. Exact matches only: a link that merely starts with these is a
-- different route and not ours to rewrite.
UPDATE notifications.notifications
   SET link = '/messages'
 WHERE link = '/adviser/messages';

UPDATE notifications.notifications
   SET link = '/profile'
 WHERE link = '/account';

-- The serial deep links. Joined on tenant_id as well as the id: `crm.contacts.id` is a global
-- serial, and resolving one across a tenant boundary would send an operator to another firm's
-- client. A row whose subject has since been deleted matches nothing and is left for the sweep
-- below to blank.
UPDATE notifications.notifications n
   SET link = '/clients/' || c.uid
  FROM crm.contacts c
 WHERE n.link ~ '^/clients/[0-9]+$'
   AND c.tenant_id = n.tenant_id
   AND c.id = substring(n.link from 10)::bigint;

UPDATE notifications.notifications n
   SET link = '/accounts/' || a.uid
  FROM platform.accounts a
 WHERE n.link ~ '^/accounts/[0-9]+$'
   AND a.tenant_id = n.tenant_id
   AND a.id = substring(n.link from 11)::bigint;

-- Anything still carrying a serial names a record that no longer exists (or never did in this
-- tenant). NULL, not the listing page: a link is a promise to open the thing the notice is about,
-- and sending the reader somewhere adjacent is a worse answer than sending them nowhere -- the
-- bell renders a row with no link as plain text, which reads honestly.
UPDATE notifications.notifications
   SET link = NULL
 WHERE link ~ '^/(clients|accounts)/[0-9]+$';

-- Hand the role back. `SET LOCAL` lasts the whole TRANSACTION, not the statement, and alembic
-- stamps `alembic_version` in this same transaction -- as service_role, which has no privilege on
-- it. Without this the data lands and the migration then dies on the stamp, so the run reports
-- failure and the next one replays it.
RESET ROLE;
