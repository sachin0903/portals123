-- 0270_retire_book_reviews — four book reviews leave the catalogue; model and fee fold into
-- their provider actions (docs/reviews.md, 22 Oct 2026).
--
-- EXPAND ONLY. This runs before the new release is rolled out (deploy.yml migrates first), so
-- nothing here may take away what the running release reads: the model / fee book policy rows
-- stay (the old code holds on them; the new code reads them as the pair of the provider row),
-- and the provider rows gain what the book rows carried. The contract step — retiring a book
-- row — happens in code, on the first save of its provider row (`actions/runs.py::set_policy`).

SET LOCAL lock_timeout = '5s';

-- 1. A hold on a retired key has no apply left to run it, ever: close every non-terminal root
--    (waiting, scheduled, parked, crashed) and every open node under it as cancelled, with the
--    reason on the run and a run.cancelled event so its timeline ends the way a cancel does.
--    A root RUNNING at this instant keeps its lease; the sweep crashes it and this never
--    revives it (the registry no longer knows the key).
CREATE TEMP TABLE retired_roots ON COMMIT DROP AS
  SELECT id, tenant_id FROM baton.runs
  WHERE parent_run_id IS NULL
    AND action_key IN ('instruction.raised', 'instruction.cancelled',
                       'account.regular_movements_changed', 'account.beneficiaries_changed')
    AND state_type IN ('paused', 'scheduled', 'pending', 'failed', 'crashed');

UPDATE baton.runs r
SET state_type = 'cancelled',
    state_name = 'Cancelled',
    state_message = 'This kind of review was retired; the change was not applied.',
    state_at = now(),
    ended_at = now(),
    updated_at = now(),
    lease_token = NULL,
    lease_owner = NULL,
    lease_until = NULL
WHERE (r.id IN (SELECT id FROM retired_roots) OR r.root_run_id IN (SELECT id FROM retired_roots))
  AND r.state_type IN ('paused', 'scheduled', 'pending', 'failed', 'crashed');

INSERT INTO baton.events (tenant_id, run_id, root_run_id, event_type, actor_id, payload)
SELECT tenant_id, id, id, 'run.cancelled', NULL,
       '{"note": "This kind of review was retired; the change was not applied."}'::jsonb
FROM retired_roots;

-- 2. Their firm policies go with them (policy_roles cascades).
DELETE FROM actions.policies
WHERE action_key IN ('instruction.raised', 'instruction.cancelled',
                     'account.regular_movements_changed', 'account.beneficiaries_changed');

-- 3. What the model / fee book rows carry is carried onto the provider row the editor shows —
--    the approval switch and its roles, the reason switch, the notify channels — ORed in, so a
--    firm that configured either row keeps what it configured. The book rows stay (see above).
UPDATE actions.policies p
SET approval_enabled = p.approval_enabled OR b.approval_enabled,
    approval_role_id = COALESCE(p.approval_role_id, b.approval_role_id),
    require_justification = p.require_justification OR b.require_justification,
    notify_in_app = p.notify_in_app OR b.notify_in_app,
    notify_email = p.notify_email OR b.notify_email,
    updated_at = now()
FROM actions.policies b
JOIN (VALUES ('account.model_changed', 'model.attach'),
             ('account.fee_packages_changed', 'fee.attach')) AS m(old_key, new_key)
  ON m.old_key = b.action_key
WHERE b.provider IS NULL
  AND p.tenant_id = b.tenant_id AND p.action_key = m.new_key AND p.provider IS NULL;

INSERT INTO actions.policies (tenant_id, action_key, provider, enabled, approval_enabled,
                              approval_role_id, require_justification, notify_in_app, notify_email)
SELECT b.tenant_id, m.new_key, NULL, true, b.approval_enabled, b.approval_role_id,
       b.require_justification, b.notify_in_app, b.notify_email
FROM actions.policies b
JOIN (VALUES ('account.model_changed', 'model.attach'),
             ('account.fee_packages_changed', 'fee.attach')) AS m(old_key, new_key)
  ON m.old_key = b.action_key
WHERE b.provider IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM actions.policies p
    WHERE p.tenant_id = b.tenant_id AND p.action_key = m.new_key AND p.provider IS NULL
  );

-- Approver roles come across only from a book row whose approval is ON: the editor used to
-- keep 'approve' rows when the switch was turned off, and those must not switch the provider
-- row's approval back on.
INSERT INTO actions.policy_roles (policy_id, role_id, kind)
SELECT p.id, g.role_id, 'approve'
FROM actions.policy_roles g
JOIN actions.policies b ON b.id = g.policy_id AND b.provider IS NULL AND b.approval_enabled
JOIN (VALUES ('account.model_changed', 'model.attach'),
             ('account.fee_packages_changed', 'fee.attach')) AS m(old_key, new_key)
  ON m.old_key = b.action_key
JOIN actions.policies p
  ON p.tenant_id = b.tenant_id AND p.action_key = m.new_key AND p.provider IS NULL
WHERE g.kind = 'approve'
ON CONFLICT DO NOTHING;
