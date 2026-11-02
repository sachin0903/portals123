/*
interlace:
  materialise: view
  depends_on: [ref.reference_current, ref.firms]
  tags: [sei, ref, fanout]
  description: >
    SEI subfirms, each owned by one firm — the second level of the tenant fan-out
    key. The admin subfirm_ids multi-select reads this, filtered by firm.
*/

-- Column is `subfirm_id`, NOT the `subfirms_id` the cherry-picked
-- transformations/sql/subfirms.sql emitted: every other model, the 004 migration
-- and the fan-out predicate all spell it `subfirm_id`.
--
-- Coverage is uneven: the current drop has subfirms for five of the six firms
-- (3106 has two, onshore + offshore; 3102 has none), so a tenant must be
-- configurable with no subfirm at all — hence subfirm_ids is optional in the
-- enablement config and an empty list means "the whole firm".
SELECT
    s.code            AS subfirm_id,
    s.description     AS subfirm_name,
    s.firm_id,
    f.firm_name,
    s.as_of_date
FROM ref.reference_current s
LEFT JOIN ref.firms f ON f.firm_id = s.firm_id
WHERE s.type = 'SUBFIRM'
