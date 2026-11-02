-- A step is addressed from a URL, so it needs a public id. The serial stays
-- inside the queue.

SET LOCAL lock_timeout = '5s';

ALTER TABLE actions.steps
  ADD COLUMN uid text;

-- actions.steps is FORCE ROW LEVEL SECURITY. The migrator is not BYPASSRLS, so this
-- UPDATE would match no rows and the NOT NULL below would then fail on the nulls it
-- could not see. service_role bypasses that (0208's precedent).
SET LOCAL ROLE service_role;
UPDATE actions.steps SET uid = public.nanoid(12) WHERE uid IS NULL;
RESET ROLE;

ALTER TABLE actions.steps
  ALTER COLUMN uid SET DEFAULT public.nanoid(12),
  ALTER COLUMN uid SET NOT NULL;

CREATE UNIQUE INDEX actions_steps_uid_idx ON actions.steps (uid);
