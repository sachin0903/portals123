/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: raw.beneficiaries
  tags: [morgan_lloyd, entity, conformed]
  description: >
    Nomination-of-beneficiary records, conformed. Was a bare `select *` view with no key.
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: key
*/

-- Morgan Lloyd's org axis is a single Microsoft tenant — one Dynamics/Azure tenant per firm —
-- so key is that tenant id and lineage is the one-element path containing it. Unlike SEI
-- there is no hierarchy to walk: the firm IS the root and the leaf. Stamped on every row by
-- the extractors (`mlTenantId`), because a multi-firm deployment syncs each connection in turn
-- and a row has to say which connection it came from.
--
-- `cam_beneficiarystatus` 0 means removed. Removed nominations are KEPT rather than filtered:
-- who was nominated and when it stopped being true is exactly the question a death-benefit
-- decision has to answer, so the flag is published and the filtering is the caller's.
WITH current AS (
    SELECT * FROM raw.beneficiaries
    QUALIFY row_number() OVER (PARTITION BY cam_nominationofbeneficiaryid
                               ORDER BY modifiedon DESC NULLS LAST) = 1
)
SELECT
    b.cam_nominationofbeneficiaryid                 AS external_id,
    b.ml_tenant_id                                  AS key,
    [b.ml_tenant_id]                                AS lineage,
    try_cast(b.modifiedon AS timestamp)             AS source_updated_at,
    b.cam_name                                      AS name,
    b.cam_nameofbeneficiary                         AS beneficiary_name,
    b.cam_firstname                                 AS first_name,
    b.cam_surname                                   AS surname,
    try_cast(b.cam_dateofbirth AS date)             AS date_of_birth,
    CASE try_cast(b.statecode AS integer) WHEN 0 THEN 'active' WHEN 1 THEN 'inactive' END
                                                    AS status,
    b.statecode                                     AS status_raw,
    try_cast(b.createdon AS timestamp)              AS created_at,
    b.ml_tenant_id,
    current_date                                    AS as_of_date,
    to_json(b)                                      AS data
FROM current b
