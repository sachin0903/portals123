-- One prior illustration is the reviewed basis for a new SIPP account. The illustration's own
-- account_id is mutable as runs are added later, so the account keeps the onboarding provenance.
ALTER TABLE platform.illustrations
  ADD CONSTRAINT illustrations_tenant_id_key UNIQUE (tenant_id, id);

ALTER TABLE platform.accounts ADD COLUMN onboarding_illustration_id bigint;

ALTER TABLE platform.accounts
  ADD CONSTRAINT accounts_onboarding_illustration_tenant_fkey
  FOREIGN KEY (tenant_id, onboarding_illustration_id)
  REFERENCES platform.illustrations (tenant_id, id)
  ON DELETE SET NULL (onboarding_illustration_id);

CREATE INDEX accounts_onboarding_illustration_idx
  ON platform.accounts (onboarding_illustration_id)
  WHERE onboarding_illustration_id IS NOT NULL;

COMMENT ON COLUMN platform.accounts.onboarding_illustration_id IS
  'The prior SIPP illustration this account was onboarded against.';
