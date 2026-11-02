-- ============================================================================
-- 0209_inbound_per_provider -- one landing schema per custodian.
--
-- `inbound.{uid}__{entity}` was one table for every pipeline. Each daemon
-- ReplaceInPlace-writes it, and the merge reads it later, stamping whatever rows
-- are sitting there with the provider it was called as. On 27 Sep 2026 SEI's
-- merge ran three seconds after Seccl's and created 304 contacts that were
-- Seccl's people, plus 52 of Seccl's models.
--
-- The provider cannot go in the TABLE name. `{uid}__third_party_providers` is
-- already 60 of Postgres's 63 bytes, and a silent truncate would make two
-- entities share one table. The schema is a separate identifier:
-- `inbound_seccl`, `inbound_sei`, `inbound_morgan_lloyd`.
--
-- The old `inbound` schema stays. Its tables are the previous runs' landing
-- data and nothing reads them; dropping tables book_writer owns is not this
-- migration's job. A new custodian is a new schema in the same change that
-- adds them to `platform.provider_code`.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS inbound_seccl;
CREATE SCHEMA IF NOT EXISTS inbound_sei;
CREATE SCHEMA IF NOT EXISTS inbound_morgan_lloyd;

COMMENT ON SCHEMA inbound_seccl IS
  'Seccl pipeline landing tables (inbound_seccl.{uid}__{entity}). Transient; the request path has no grant.';
COMMENT ON SCHEMA inbound_sei IS
  'SEI pipeline landing tables (inbound_sei.{uid}__{entity}). Transient; the request path has no grant.';
COMMENT ON SCHEMA inbound_morgan_lloyd IS
  'Morgan Lloyd pipeline landing tables (inbound_morgan_lloyd.{uid}__{entity}). Transient; the request path has no grant.';

GRANT USAGE, CREATE ON SCHEMA inbound_seccl, inbound_sei, inbound_morgan_lloyd TO book_writer;
GRANT USAGE ON SCHEMA inbound_seccl, inbound_sei, inbound_morgan_lloyd TO service_role;

ALTER DEFAULT PRIVILEGES FOR ROLE book_writer IN SCHEMA inbound_seccl
  GRANT SELECT ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE book_writer IN SCHEMA inbound_sei
  GRANT SELECT ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE book_writer IN SCHEMA inbound_morgan_lloyd
  GRANT SELECT ON TABLES TO service_role;
