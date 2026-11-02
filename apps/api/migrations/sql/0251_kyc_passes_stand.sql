-- 0251_kyc_passes_stand -- an identity or AML pass does not lapse.
--
-- A passed identity or AML check (crm.kyc_checks) was valid for 365 days, or for the `validity_days`
-- a firm set on the step, and a new journey after that checked the client again. It now stands until
-- a later check replaces it: ongoing AML monitoring and the re-screen on a change of name, date of
-- birth or nationality (`compliance.mark_screen_stale`, 0223) keep a screen current, not a timer.
-- The engine writes no `valid_until` on an identity or AML pass (`app.flows.kyc.compute_valid_until`)
-- and reads a NULL as no end (`kyc.is_valid`). This clears the horizon every earlier pass was
-- stamped with. Terms acceptances keep theirs.
--
-- service_role: crm.kyc_checks forces row-level security, and `migrator` does not bypass it, so
-- without the role this UPDATE would match no row and still succeed.

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE service_role;

UPDATE crm.kyc_checks
   SET valid_until = NULL
 WHERE kind IN ('identity', 'aml')
   AND valid_until IS NOT NULL;

RESET ROLE;
