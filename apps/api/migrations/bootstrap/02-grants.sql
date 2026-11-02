-- 02-grants.sql -- superuser bootstrap, run once per environment against the `portals`
-- database, AFTER 01-roles.sql has created the roles (psql; the local compose auto-runs
-- it on first init). Idempotent (GRANTs are).
--
-- 01-roles.sql is database-agnostic (it doesn't know the database name); these are the
-- database-specific grants the migrator needs to create schemas/objects when
-- `moon run api:migrate` runs. Managed environments where the migrator owns the database
-- don't need them.
GRANT CREATE ON DATABASE portals TO migrator;
GRANT CREATE, USAGE ON SCHEMA public TO migrator;
