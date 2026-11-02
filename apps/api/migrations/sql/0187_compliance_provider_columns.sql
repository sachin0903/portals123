-- ============================================================================
-- 0187_compliance_provider_columns.sql — query columns for each provider
-- resource. `body` stays the untouched payload. A client or check that
-- arrives before it matches `crm.contacts` still has `tenant_id` and
-- `contact_id` null. Metadata stays in `body` only. The firm API does not
-- select these columns.
--
-- Nullable ADD COLUMN is metadata-only. The index is not built concurrently:
-- Alembic runs this revision inside a transaction.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

ALTER TABLE compliance.clients
  ADD COLUMN entity_name text,
  ADD COLUMN email text,
  ADD COLUMN mobile text,
  ADD COLUMN telephone text,
  ADD COLUMN external_id text,
  ADD COLUMN joined_date date,
  ADD COLUMN client_type text,
  ADD COLUMN first_name text,
  ADD COLUMN middle_name text,
  ADD COLUMN last_name text,
  ADD COLUMN dob date,
  ADD COLUMN gender text,
  ADD COLUMN nationality text,
  ADD COLUMN birth_country text,
  ADD COLUMN ssn text,
  ADD COLUMN social_insurance_number text,
  ADD COLUMN national_identity_number text,
  ADD COLUMN tax_identification_number text,
  ADD COLUMN company_name text,
  ADD COLUMN website text,
  ADD COLUMN registration_number text,
  ADD COLUMN incorporation_country text,
  ADD COLUMN incorporation_type text;

CREATE INDEX compliance_clients_external_id_idx
  ON compliance.clients (external_id)
  WHERE external_id IS NOT NULL;

ALTER TABLE compliance.addresses
  ADD COLUMN address_type text,
  ADD COLUMN property_number text,
  ADD COLUMN building_name text,
  ADD COLUMN to_date date;

ALTER TABLE compliance.documents
  ADD COLUMN document_type text,
  ADD COLUMN document_number text,
  ADD COLUMN issuing_state text;

ALTER TABLE compliance.live_photos
  ADD COLUMN perform_liveness_check boolean,
  ADD COLUMN content_type text,
  ADD COLUMN size_bytes bigint,
  ADD COLUMN download_link text;

ALTER TABLE compliance.checks
  ADD COLUMN provider_type text,
  ADD COLUMN provider_status text,
  ADD COLUMN outcome text,
  ADD COLUMN entity_name text,
  ADD COLUMN enable_monitoring boolean,
  ADD COLUMN client_consent boolean,
  ADD COLUMN provider_document_id text,
  ADD COLUMN provider_address_id text,
  ADD COLUMN provider_live_photo_id text,
  ADD COLUMN provider_live_video_id text;
