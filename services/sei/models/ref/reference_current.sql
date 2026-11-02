/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: [type, code, firm_id]
  depends_on: raw.reference
  tags: [sei, ref]
  description: >
    Latest snapshot of the SEI Reference report — the code/description dimension
    carrier every mart joins to.
*/

-- The Reference report is one long TYPE-discriminated table:
--   TYPE|CODE|DESCRIPTION|FIRM_ID|QUALIFIER_1|QUALIFIER_2|QUALIFIER_3
-- raw.reference is append-only across drops, so a code that was renamed appears
-- once per day it was delivered. Take only the most recent drop's rows: SEI sends
-- the complete reference set every night, so the newest file IS the current state
-- and there is no need to reconcile older ones.
WITH latest AS (SELECT max(dt) AS dt FROM raw.reference)
SELECT
    r."TYPE"          AS type,
    r."CODE"          AS code,
    r."DESCRIPTION"   AS description,
    r."FIRM_ID"       AS firm_id,
    r."QUALIFIER_1"   AS qualifier_1,
    r."QUALIFIER_2"   AS qualifier_2,
    r."QUALIFIER_3"   AS qualifier_3,
    r.dt              AS as_of_date
FROM raw.reference r
JOIN latest ON r.dt = latest.dt
WHERE r."TYPE" IS NOT NULL AND r."CODE" IS NOT NULL
