-- 0252_identity_photos_by_check -- an identity photo is kept until its check is decided.
--
-- 0235 held a portal client's photos only while their identity step was open, and nobody viewed
-- them. They are now Graphene's backup of what ComplyCube holds: the AML console and the firm's
-- compliance page show our copy of any photo ComplyCube cannot give. So a photo names the check it
-- was sent for (`check_id`), and is kept while its step is open OR that check is undecided — a
-- check held for review keeps its photos even if the journey around it was cancelled. A photo
-- uploaded for a client with no journey step waiting (`app/complycube/uploads.py`) has a check and
-- no step.
--
-- Still service_role only and never audited (0235).

SET LOCAL lock_timeout = '5s';

ALTER TABLE compliance.identity_photos
  ADD COLUMN check_id int REFERENCES compliance.checks(id) ON DELETE CASCADE,
  ALTER COLUMN case_id DROP NOT NULL,
  ALTER COLUMN step_key DROP NOT NULL,
  ADD CONSTRAINT identity_photos_owner_check
    CHECK ((case_id IS NOT NULL AND step_key IS NOT NULL) OR check_id IS NOT NULL);

CREATE INDEX identity_photos_check_idx
  ON compliance.identity_photos (check_id) WHERE check_id IS NOT NULL;

COMMENT ON TABLE compliance.identity_photos IS
  'A portal client''s identity photos, sealed with the API master key: Graphene''s backup of what '
  'ComplyCube holds, kept while the identity step is open or the check they were sent for is '
  'undecided. Service_role only; never audited.';
COMMENT ON COLUMN compliance.identity_photos.check_id IS
  'The identity check the photo was sent for, once there is one. NULL until the send.';
