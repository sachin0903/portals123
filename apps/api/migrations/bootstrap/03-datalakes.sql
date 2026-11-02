-- 03-datalakes.sql -- superuser bootstrap, run once per environment AFTER 01/02 (psql;
-- the local compose auto-runs it on first init). Idempotent. Creates the DATALAKES
-- database — the DuckLake catalogue host (docs/datalake.md): one metadata schema per
-- tenant (named by the tenant uid, created by the API's storage provisioning), holding
-- DuckLake's ducklake_* tables. Parquet data lives in the per-tenant S3 buckets, never here.
--
-- Two LOGIN roles, mirroring the OLTP app/migrator split (docs/data-architecture.md §5):
--   lake_writer -- the ETL / provisioning role: creates tenant schemas, owns the DuckLake
--                  metadata tables (DuckLake writes its catalogue as this role).
--   lake_reader -- read-only: what app/lake attaches as (READ_ONLY). Default privileges
--                  below make every table lake_writer creates readable.
-- Neither role can reach the portals OLTP database (no grants there), and the OLTP roles
-- get nothing here — the grant invariant between the privilege islands.
-- Dev passwords; managed environments rotate them out of band (like 01's app/migrator).

DO $$ BEGIN CREATE ROLE lake_writer LOGIN PASSWORD 'lake_writer'; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE lake_reader LOGIN PASSWORD 'lake_reader'; EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- CREATE DATABASE cannot run in a DO block/transaction; \gexec makes it conditional.
SELECT 'CREATE DATABASE datalakes OWNER lake_writer'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'datalakes')\gexec

\connect datalakes

-- lake_writer owns the DB (creates tenant schemas). lake_reader can connect and, via the
-- default privileges, read whatever lake_writer creates — per-schema USAGE is granted by
-- the API when it creates each tenant schema (ensure_tenant_catalog).
GRANT CONNECT ON DATABASE datalakes TO lake_reader;
REVOKE ALL ON DATABASE datalakes FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE lake_writer GRANT SELECT ON TABLES TO lake_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE lake_writer GRANT USAGE ON SEQUENCES TO lake_reader;
