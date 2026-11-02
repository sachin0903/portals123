-- ============================================================================
-- 0028_tasks_polish.sql — tasks phase 4: notification stamps + book-scoped staff visibility.
--
-- (1) Assign-time + near-due email notices are driven by a worker-side scanner
--     (app/tasks/notifier.py) rather than request-path sends: `notified_at` /
--     `due_soon_notified_at` are its idempotency stamps (set on successful delivery, so a failed
--     send retries on the next scan). Partial indexes keep both scans O(pending).
--
-- (2) Staff visibility aligns with the documents pattern (0021_documents_scope): full-book staff
--     (contacts:read_all) see every task in the tenant; book-scoped staff see tasks with no
--     client assignee (staff/role work) or assigned to a contact within their own book; clients
--     keep seeing exactly their own. WITH CHECK stays tenant-wide like documents_write — creation
--     targeting is validated at the application layer (_validate_assignee runs under the
--     caller's RLS, so an out-of-book contact is already invisible to a book-scoped creator).
-- ============================================================================

ALTER TABLE tasks.tasks
  ADD COLUMN notified_at timestamptz,
  ADD COLUMN due_soon_notified_at timestamptz;

CREATE INDEX tasks_unnotified_idx ON tasks.tasks(id)
  WHERE notified_at IS NULL AND status IN ('open', 'in_progress');
CREATE INDEX tasks_due_scan_idx ON tasks.tasks(due_date)
  WHERE due_soon_notified_at IS NULL AND due_date IS NOT NULL
    AND status IN ('open', 'in_progress');

DROP POLICY tasks_read ON tasks.tasks;
DROP POLICY tasks_write ON tasks.tasks;

CREATE POLICY tasks_read ON tasks.tasks FOR SELECT TO authenticated USING (
  -- Full-book staff: every task in the tenant.
  (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
  -- Book-scoped staff: staff/role work (no client assignee), or a client in their own book.
  OR (
    tenant_id = ANY (auth.tenant_lineage())
    AND (assignee_contact_id IS NULL OR assignee_contact_id = ANY (auth.contact_scope()))
  )
  -- Clients: their own action items only (lineage is empty in the client portal).
  OR assignee_contact_id = ANY (auth.contact_scope())
);

CREATE POLICY tasks_write ON tasks.tasks FOR ALL TO authenticated
  USING (
    (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
    OR (
      tenant_id = ANY (auth.tenant_lineage())
      AND (assignee_contact_id IS NULL OR assignee_contact_id = ANY (auth.contact_scope()))
    )
    OR assignee_contact_id = ANY (auth.contact_scope())
  )
  WITH CHECK (
    tenant_id = ANY (auth.tenant_lineage())
    OR assignee_contact_id = ANY (auth.contact_scope())
  );
