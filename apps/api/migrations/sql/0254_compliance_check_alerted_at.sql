-- 0254_compliance_check_alerted_at -- a check records when a monitoring alert last reopened it.
--
-- A monitoring alert refers a finished AML check again: a `check.monitoring.attention` event, a
-- monitored check that was decided going back to `refer`, or a settled refer reopened by a new
-- match (`app.complycube.sync.is_alert`). The consumer stamps `alerted_at` when one lands, so the
-- console can say a client's open hits came from monitoring (`app.complycube.attention`).
--
-- Firms read `compliance.checks` through column grants (0221); the new column is not granted to
-- `authenticated`, so no firm reads it.
--
-- Backfill: the latest monitoring event on each check, or the monitoring task raised for it
-- (`complycube:check:<uid>:<event>`; a `:failed` task is a failure, not an alert), whichever is
-- later. service_role: compliance.checks forces row-level security, and `migrator` does not bypass
-- it, so without the role this UPDATE would match no row and still succeed.

SET LOCAL lock_timeout = '5s';

ALTER TABLE compliance.checks ADD COLUMN alerted_at timestamptz;

SET LOCAL ROLE service_role;

UPDATE compliance.checks ch
   SET alerted_at = alerts.at
  FROM (
    SELECT check_id, max(at) AS at
      FROM (
        SELECT x.id AS check_id, e.received_at AS at
          FROM compliance.events e
          JOIN compliance.checks x ON x.provider_check_id = e.payload -> 'payload' ->> 'id'
         WHERE e.event_type = 'check.monitoring.attention'
        UNION ALL
        SELECT x.id, t.created_at
          FROM tasks.tasks t
          JOIN compliance.checks x ON x.uid = split_part(t.source_ref, ':', 3)
         WHERE t.source_ref LIKE 'complycube:check:%:%'
           AND split_part(t.source_ref, ':', 4) NOT IN ('', 'failed')
      ) found
     GROUP BY check_id
  ) alerts
 WHERE ch.id = alerts.check_id
   AND ch.kind = 'aml';

RESET ROLE;
