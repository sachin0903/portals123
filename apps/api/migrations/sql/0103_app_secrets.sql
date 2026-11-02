-- ============================================================================
-- 0099_app_secrets.sql -- per-application client secrets (portal-via-application).
--
-- Each WorkOS APPLICATION has its own client_id AND its own client secret. A portal signs in through
-- its application, so it must authenticate with THAT application's secret — using the environment's
-- api key with a different application's client id fails `invalid_client` ("client secret from a
-- different application"). So we store a secret per portal app, Fernet-encrypted like the env key,
-- and env_config_by_host serves the matching one for the host's portal.
--
-- service_role-only, like the rest of auth.environment_secrets (no authenticated grant/policy).
-- ============================================================================

ALTER TABLE auth.environment_secrets
  ADD COLUMN IF NOT EXISTS client_portal_secret_ct text,   -- Fernet ciphertext of the Client Portal app secret
  ADD COLUMN IF NOT EXISTS platform_secret_ct      text,   -- ... the Platform app secret
  ADD COLUMN IF NOT EXISTS operations_secret_ct    text;   -- ... the Internal Operations app secret

-- Redact the new secret columns from the audit trail (audit.record already redacts the env key).
-- (audit.record's redact list is a literal array in 0001; these follow the same intent — the
-- ciphertext is not the secret, but keep it out of the trail on principle, as the env key is.)
