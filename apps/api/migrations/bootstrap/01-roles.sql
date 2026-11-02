-- ============================================================================
-- 01-roles.sql -- one-time role/grant bootstrap, run by a SUPERUSER before the first
-- migration (e.g. `psql -f migrations/bootstrap/01-roles.sql`). It is idempotent.
--
-- Why this exists (docs/api.md, docs/identity-and-access.md):
--   * The RLS *principals* (anon / authenticated / service_role) are NOLOGIN roles
--     the policies grant to. 0001_auth_core also tries to create them, but only if the
--     migrator has CREATEROLE -- in managed Postgres it usually does not, so they are
--     created here by the superuser instead.
--   * The two *login* roles the app uses:
--       - migrator : owns the schema, runs Alembic DDL (MIGRATION_DATABASE_URL)
--       - app      : DML-only, RLS-bound request path (DATABASE_URL)
--   * The request path runs as `app` but RLS policies are `TO authenticated`, so `app`
--     must be a MEMBER of `authenticated` (and `service_role` for system/worker work)
--     and `SET LOCAL ROLE authenticated` per transaction (see app/db.py::tenant_session).
--
-- Dev/local credentials match apps/api/.env.example (app:app, migrator:migrator). In a
-- managed environment, provision these roles + passwords out of band and run only the
-- GRANT ... TO app membership lines.
-- ============================================================================

-- RLS principals (NOLOGIN). service_role bypasses RLS.
DO $$ BEGIN CREATE ROLE anon NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE authenticated NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE service_role NOLOGIN BYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Login roles. migrator owns + DDLs; app is the RLS-bound request role.
DO $$ BEGIN CREATE ROLE migrator LOGIN PASSWORD 'migrator' CREATEDB; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE app LOGIN PASSWORD 'app'; EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- book_writer: the provider pipelines' login. They land a tenant's conformed slice in `inbound`
-- and call platform.merge_inbound() to upsert the book (docs/platform-model.md).
--
-- BYPASSRLS is unavoidable here, and saying so plainly is better than implying it was avoided: a
-- merge writes rows for EVERY tenant, and platform.* carries FORCE ROW LEVEL SECURITY, which
-- applies even to a table owner. There is no policy that expresses "all tenants" without being
-- equivalent to no policy. It is why materialise.py runs as service_role today.
--
-- What is actually reduced is GRANT SCOPE, in the migration that creates `inbound`: book_writer
-- reaches the merged relations and nothing else. service_role has ALL on auth.users, documents.*,
-- aml.*, flows.* and storage.*; book_writer has none of them, so a compromised pipeline
-- credential cannot read a client's identity documents or a firm's screening outcomes.
DO $$ BEGIN CREATE ROLE book_writer LOGIN PASSWORD 'book_writer' BYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- The migration that sets up `inbound` declares default privileges FOR ROLE book_writer, so the
-- migrator has to be a member of it — `ALTER DEFAULT PRIVILEGES FOR ROLE x` is refused otherwise
-- ("permission denied to change default privileges"). Same shape, and same reason, as the
-- service_role membership above; membership does not leak BYPASSRLS to the migrator, because role
-- attributes are not inherited, only privileges.
GRANT book_writer TO migrator;

-- The request role (`app`) must be able to assume the RLS principals.
GRANT authenticated TO app;
GRANT service_role  TO app;

-- The migrator owns the schema but FORCE RLS would also bind it, so the cross-RLS
-- SECURITY DEFINER helpers (auth.resolve_claims, the lineage triggers, audit.record)
-- are reassigned to the BYPASSRLS service_role inside 0001. For that ALTER ... OWNER to
-- succeed the migrator must be a member of service_role (membership does NOT leak
-- BYPASSRLS to the migrator itself -- attributes aren't inherited, only privileges).
GRANT service_role TO migrator;

-- The DocumentWorker also connects as the migrator (app/worker.py builds its engine from
-- MIGRATION_DATABASE_URL), and a tenant-authored SQL dataset is deliberately executed with
-- `SET LOCAL ROLE authenticated` + minted claims so RLS still scopes it even though the worker's
-- own login exceeds RLS (app/render/resolve.py::_resolve_sql). Without this membership that SET ROLE
-- fails and EVERY SQL dataset returns nothing at render — silently, because the resolver reports the
-- failure rather than crashing. So this GRANT is what makes RLS-scoped dataset execution possible;
-- it is not an escalation (migrator is already a member of the BYPASSRLS service_role above).
GRANT authenticated TO migrator;

-- Let the app role use objects the migrator creates. FORCE RLS still constrains it;
-- table-level GRANTs in the migrations decide what `authenticated` may actually touch.
GRANT USAGE ON SCHEMA public TO app, authenticated, service_role;

-- NOTE: the migrator must be able to create schemas in this database. When the
-- migrator is not the database owner, the superuser should additionally run:
--   GRANT CREATE ON DATABASE <db> TO migrator;
