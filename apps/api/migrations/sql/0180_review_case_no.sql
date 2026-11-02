-- 0180_review_case_no: a spoken case number per firm (RV-12), not the nanoid.
--
-- The public uid stays the route handle. Staff say "review 12" on the phone; a
-- 12-character nanoid is not that. Numbered per tenant, in proposed order.

SET LOCAL lock_timeout = '5s';

ALTER TABLE reviews.requests ADD COLUMN case_no int;

-- FORCE RLS on reviews.requests: a backfill as migrator sees zero rows and SET NOT NULL fails.
SET LOCAL ROLE service_role;

WITH numbered AS (
  SELECT id, ROW_NUMBER() OVER (PARTITION BY tenant_id ORDER BY proposed_at, id) AS n
  FROM reviews.requests
)
UPDATE reviews.requests r
   SET case_no = numbered.n
  FROM numbered
 WHERE r.id = numbered.id;

RESET ROLE;

ALTER TABLE reviews.requests ALTER COLUMN case_no SET NOT NULL;

CREATE UNIQUE INDEX reviews_requests_case_no
  ON reviews.requests (tenant_id, case_no);

COMMENT ON COLUMN reviews.requests.case_no IS
  'Per-firm sequential case number, displayed as RV-{n}. The uid remains the public route id.';
