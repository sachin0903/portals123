-- Approval is a set of roles, the same shape as who may run the action.
-- ``actions.runs.approval_role_id`` stays as the first snapshotted role so a
-- reader of that column still sees one. ``actions.run_approvers`` is the set
-- the decision checks.

SET LOCAL lock_timeout = '5s';

ALTER TABLE actions.policy_roles
  ADD COLUMN kind text NOT NULL DEFAULT 'invoke';

ALTER TABLE actions.policy_roles
  DROP CONSTRAINT policy_roles_pkey;

ALTER TABLE actions.policy_roles
  ADD PRIMARY KEY (policy_id, role_id, kind);

ALTER TABLE actions.policy_roles
  ADD CONSTRAINT policy_roles_kind_check CHECK (kind IN ('invoke', 'approve'));

CREATE TABLE actions.run_approvers (
  run_id bigint NOT NULL REFERENCES actions.runs(id) ON DELETE CASCADE,
  role_id int NOT NULL REFERENCES auth.roles(id) ON DELETE CASCADE,
  PRIMARY KEY (run_id, role_id)
);

ALTER TABLE actions.run_approvers ENABLE ROW LEVEL SECURITY;

CREATE POLICY actions_run_approvers_all ON actions.run_approvers FOR ALL TO authenticated
  USING (EXISTS (
    SELECT 1 FROM actions.runs r
    WHERE r.id = run_id AND r.tenant_id = ANY (auth.tenant_lineage())
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM actions.runs r
    WHERE r.id = run_id AND r.tenant_id = ANY (auth.tenant_lineage())
  ));

GRANT SELECT, INSERT, DELETE ON actions.run_approvers TO authenticated;
GRANT ALL ON actions.run_approvers TO service_role;

ALTER TABLE actions.run_approvers FORCE ROW LEVEL SECURITY;
