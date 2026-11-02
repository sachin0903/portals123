-- 0245_one_adviser_per_client — a client has ONE adviser and any number of other staff.
--
-- crm.client_advisers (0002) is a client's team: one row per member of staff, with a free-text
-- `role`. Nothing limited how many advisers a client had, and nothing fixed what `role` said — the
-- estate held `lead`, `adviser`, `advisor`, `support`, `reviewer` and NULL for the same few ideas.
-- So "the client's adviser" was whatever each reader's ORDER BY picked: the lead in four places,
-- the alphabetically-first name or the oldest row in ten, and in messaging no order at all.
--
-- The rule now:
--   * `role` is one of adviser | paraplanner | administrator | support, and NOT NULL.
--   * At most one `adviser` row per client — a partial unique index, so "the adviser" is a lookup
--     (`role = 'adviser'`) rather than a tie-break.
--   * Any number of rows with the other roles. Every row still feeds book scope
--     (auth.resolve_claims), so a paraplanner on the team sees the client.
-- That the adviser's login is `auth.users.is_adviser`, and that everyone else on a team is staff,
-- is checked by `contacts.assign_adviser`: a CHECK cannot reach across to auth.users.
--
-- EXISTING ROWS ARE RE-LABELLED, NEVER DELETED. Per client, the one adviser is the first of: a
-- live is_adviser login before a deleted one, then a row already called lead/adviser/advisor, then
-- the oldest row — and never a row already called paraplanner/admin. Every other row keeps its
-- place on the team: `paraplan…` → paraplanner, `admin…` → administrator, anything else →
-- support. A second adviser therefore becomes support and keeps their access; the firm removes them
-- if they should no longer see the client.
--
-- DML as service_role first (FORCE RLS: the migrator reads no rows), then DDL as the migrator —
-- the two must not be interleaved (0137).

SET LOCAL lock_timeout = '5s';

SET LOCAL ROLE service_role;

WITH normalised AS (
  SELECT ca.id,
         ca.contact_id,
         ca.role,
         lower(btrim(coalesce(ca.role, ''))) AS r,
         u.is_adviser,
         u.deleted_at
    FROM crm.client_advisers ca
    JOIN auth.users u ON u.id = ca.adviser_id
),
chosen AS (
  SELECT DISTINCT ON (contact_id) id
    FROM normalised
   WHERE is_adviser
     AND r NOT LIKE 'paraplan%'
     AND r NOT LIKE 'admin%'
   ORDER BY contact_id,
            (deleted_at IS NULL) DESC,
            (r IN ('lead', 'adviser', 'advisor')) DESC,
            id
),
target AS (
  SELECT n.id,
         n.role AS old_role,
         CASE
           WHEN n.id IN (SELECT id FROM chosen) THEN 'adviser'
           WHEN n.r LIKE 'paraplan%' THEN 'paraplanner'
           WHEN n.r LIKE 'admin%' THEN 'administrator'
           ELSE 'support'
         END AS new_role
    FROM normalised n
)
UPDATE crm.client_advisers ca
   SET role = t.new_role
  FROM target t
 WHERE t.id = ca.id
   AND t.old_role IS DISTINCT FROM t.new_role;

RESET ROLE;

ALTER TABLE crm.client_advisers
  ALTER COLUMN role SET NOT NULL,
  ADD CONSTRAINT client_advisers_role_known
    CHECK (role IN ('adviser', 'paraplanner', 'administrator', 'support'));

CREATE UNIQUE INDEX client_advisers_one_adviser_key
  ON crm.client_advisers (contact_id)
  WHERE role = 'adviser';

COMMENT ON TABLE crm.client_advisers IS
  'A client''s team: one row per member of staff, which is also what scopes an own-book login '
  '(auth.resolve_claims). At most one row is the adviser (client_advisers_one_adviser_key); any '
  'number hold the other roles.';
COMMENT ON COLUMN crm.client_advisers.role IS
  'adviser | paraplanner | administrator | support. One adviser per client, and only an '
  'auth.users.is_adviser login may be it; every other role takes any member of staff. '
  'contacts.assign_adviser checks the person, client_advisers_role_known the word.';
