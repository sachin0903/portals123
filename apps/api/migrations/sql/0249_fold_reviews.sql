-- Fold peer review into the action registry.
--
-- reviews.policies and reviews.requests were a second four-eye store beside
-- actions.policies and actions.runs. The hold, the spoken case number, the
-- justification and the decision note now live on the run. An empty approver
-- list is off; a role on the policy is on. Book doors keep calling maybe_hold.

SET LOCAL lock_timeout = '5s';

ALTER TABLE actions.runs
  ADD COLUMN justification text,
  ADD COLUMN decision_note text,
  ADD COLUMN case_no int,
  ADD COLUMN subject_type text,
  ADD COLUMN subject_id text;

ALTER TABLE actions.runs
  ADD COLUMN _search text GENERATED ALWAYS AS (
    search.normalise(
      uid,
      case_no::text,
      action_key,
      event_type,
      subject_type,
      subject_id
    )
  ) STORED;

COMMENT ON COLUMN actions.runs.justification IS
  'Why the proposer asked for the change. Required when a book action is held.';
COMMENT ON COLUMN actions.runs.decision_note IS
  'The approver''s reason, required on reject.';
COMMENT ON COLUMN actions.runs.case_no IS
  'Per-firm sequential case number, displayed as RV-{n}. Null on a run that was not held.';
COMMENT ON COLUMN actions.runs.subject_type IS
  'contact, account, or instruction — the record a book action is about.';
COMMENT ON COLUMN actions.runs.subject_id IS
  'Public uid of that record.';
COMMENT ON COLUMN actions.runs._search IS
  'Search text for a held run: uid, case number, action, event, subject. Generated.';

CREATE UNIQUE INDEX actions_runs_case_no
  ON actions.runs (tenant_id, case_no) WHERE case_no IS NOT NULL;

CREATE INDEX actions_runs_search_trgm
  ON actions.runs USING gin (_search gin_trgm_ops);

-- FORCE RLS: the migrator sees nothing. service_role bypasses it.
SET LOCAL ROLE service_role;

INSERT INTO actions.policies (
  tenant_id, action_key, provider, enabled, approval_enabled, approval_role_id
)
SELECT tenant_id, action_key, NULL, true, true, reviewer_role_id
FROM reviews.policies
WHERE mode = 'four_eye' AND reviewer_role_id IS NOT NULL
ON CONFLICT (tenant_id, action_key, provider) DO UPDATE SET
  enabled = true,
  approval_enabled = true,
  approval_role_id = EXCLUDED.approval_role_id;

INSERT INTO actions.policy_roles (policy_id, role_id, kind)
SELECT p.id, p.approval_role_id, 'approve'
FROM actions.policies p
JOIN reviews.policies q
  ON q.tenant_id = p.tenant_id AND q.action_key = p.action_key
WHERE q.mode = 'four_eye'
  AND p.provider IS NULL
  AND p.approval_role_id IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO actions.runs (
  uid, tenant_id, action_key, event_type, payload, before,
  requested_by, requested_at, approval_role_id,
  decided_by, decided_at, applied_at, contact_id,
  idempotency_key, status, created_at, updated_at,
  justification, decision_note, case_no, subject_type, subject_id
)
SELECT
  q.uid, q.tenant_id, q.action_key, q.event_type, q.payload, q.before,
  q.proposed_by, q.proposed_at, q.reviewer_role_id,
  q.decided_by, q.decided_at, q.applied_at, q.contact_id,
  q.idempotency_key,
  CASE q.status
    WHEN 'pending' THEN 'awaiting_approval'
    WHEN 'approved' THEN 'succeeded'
    ELSE q.status
  END,
  q.created_at, q.updated_at,
  q.justification, q.decision_note, q.case_no, q.subject_type, q.subject_id
FROM reviews.requests q
WHERE NOT EXISTS (
  SELECT 1 FROM actions.runs r
  WHERE r.uid = q.uid
     OR (r.tenant_id = q.tenant_id AND r.idempotency_key = q.idempotency_key)
);

WITH numbered AS (
  SELECT r.id,
         COALESCE(
           (SELECT MAX(x.case_no) FROM actions.runs x WHERE x.tenant_id = r.tenant_id),
           0
         ) + ROW_NUMBER() OVER (PARTITION BY r.tenant_id ORDER BY r.requested_at, r.id) AS n
  FROM actions.runs r
  WHERE r.status = 'awaiting_approval' AND r.case_no IS NULL
)
UPDATE actions.runs u
   SET case_no = numbered.n
  FROM numbered
 WHERE u.id = numbered.id;

INSERT INTO actions.run_approvers (run_id, role_id)
SELECT r.id, r.approval_role_id
FROM actions.runs r
WHERE r.case_no IS NOT NULL AND r.approval_role_id IS NOT NULL
ON CONFLICT DO NOTHING;

RESET ROLE;

CREATE UNIQUE INDEX actions_runs_open_subject
  ON actions.runs (tenant_id, action_key, subject_type, subject_id)
  WHERE status = 'awaiting_approval'
    AND subject_type IS NOT NULL
    AND subject_id IS NOT NULL;

DROP TABLE reviews.requests;
DROP TABLE reviews.policies;
DROP SCHEMA reviews;
