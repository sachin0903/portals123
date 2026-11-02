-- 0250_identity_upload -- who uploaded an identity check's photos, when it was not the client.
--
-- Graphene operations can upload a portal client's identity photos for them in the AML console
-- (`POST /aml/clients/{key}/identity-photos`), and so can the firm's advisers on the client's
-- compliance page (`POST /crm/contacts/{uid}/identity-photos`): the client sent a new document by
-- email, or better photos of a try. The check is run on those photos, and records who uploaded
-- them, from which side, and why. All three are NULL on a check the client sent the photos for.
--
-- Not granted to `authenticated`: 0221 grants a firm the status columns of compliance.checks one
-- by one, so a new column stays Graphene's until a grant names it.

SET LOCAL lock_timeout = '5s';

ALTER TABLE compliance.checks
  ADD COLUMN uploaded_by text,
  ADD COLUMN uploaded_via text CHECK (uploaded_via IN ('operations', 'firm')),
  ADD COLUMN upload_comment text;

COMMENT ON COLUMN compliance.checks.uploaded_by IS
  'Who uploaded the photos this identity check ran on, for the client; NULL when the client sent them.';
COMMENT ON COLUMN compliance.checks.uploaded_via IS
  'Who uploaded the photos: Graphene operations or the firm; NULL when the client sent them.';
COMMENT ON COLUMN compliance.checks.upload_comment IS
  'Why the photos were uploaded. For Graphene: never shown to the client, nor an operator''s to the firm.';
