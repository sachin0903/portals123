-- Live WorkOS sessions for the signed-in browsers list.
--
-- The events poller writes this from session.created and session.revoked. Those
-- payloads are the session object (address, user agent, status, expiry), which the
-- stream was already delivering and we were ignoring. A one-time list per person
-- fills in sessions that were already live, because the cursor does not replay
-- events from before it. After that the stream keeps the table current.
--
-- Only active rows are stored. Revoked and expired sessions are deleted. WorkOS
-- sends no separate expiry event, so a row past expires_at is left in place and
-- hidden by the read. Ending a session still calls WorkOS, which is what signs
-- the browser out.

SET LOCAL lock_timeout = '5s';

CREATE TABLE auth.sessions (
  workos_session_id text PRIMARY KEY,
  tenant_id int NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  ip_address text,
  user_agent text,
  auth_method text,
  created_at timestamptz,
  expires_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX sessions_user_idx ON auth.sessions (tenant_id, user_id);

ALTER TABLE auth.sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.sessions FORCE ROW LEVEL SECURITY;

-- A person may read their own sessions. Firm and operations profiles read as
-- service_role, which bypasses RLS. There is no tenant-wide read: a client
-- login must not see a colleague's sessions.
CREATE POLICY sessions_read_own ON auth.sessions
  FOR SELECT TO authenticated
  USING (tenant_id = auth.tenant_id() AND user_id = auth.uid());

GRANT SELECT ON auth.sessions TO authenticated;
GRANT ALL ON auth.sessions TO service_role;

COMMENT ON TABLE auth.sessions IS
  'Live WorkOS sessions. Written by the events poller. Revoke still calls WorkOS.';

-- NULL means the backfill has not listed this person yet. A revoke moves it
-- forward too, so a list already in flight cannot write that session back.
-- The backfill still lists a person only while this is NULL.
ALTER TABLE auth.users ADD COLUMN sessions_synced_at timestamptz;

COMMENT ON COLUMN auth.users.sessions_synced_at IS
  'When this person''s live sessions were copied from WorkOS. NULL until that first list.';

CREATE INDEX users_sessions_unsynced_idx
  ON auth.users (tenant_id, created_at)
  WHERE sessions_synced_at IS NULL
    AND workos_user_id IS NOT NULL
    AND deleted_at IS NULL;
