/*
interlace:
  materialise: view
  depends_on: ref.reference_current
  tags: [sei, ref, fanout]
  description: >
    SEI firms — the top level of the tenant fan-out key, and the firm list the
    API models request. One row per TYPE=FIRM_ID in the latest Reference drop.
    The admin firm_id dropdown reads this.
*/

-- For TYPE='FIRM_ID' rows, CODE and FIRM_ID are the same value (verified against
-- the p04 Reference drop), which is why joins elsewhere match subfirm.firm_id to
-- either. `code` is kept so that stays true.
SELECT
    firm_id,
    code,
    description AS firm_name,
    as_of_date
FROM ref.reference_current
WHERE type = 'FIRM_ID'
