/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: raw.transfers
  tags: [morgan_lloyd, entity, conformed]
  description: >
    Transfers / personal policies, conformed. Was a bare `select *` view with no declared key.
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
-- Only the fields confirmed present on the live entity are surfaced; `cam_personalpolicies`
-- carries 536 columns and the rest stay in `data` rather than being guessed at. A transfer
-- VALUE is conspicuously not among them — the amount lives on the linked financial
-- transaction (`_cam_financialtransactionid_value`), which is a join this layer does not
-- yet make. A scheme id is likewise absent from this entity (the linked scheme is reached
-- through the financial transaction), so no scheme_external_id is published.
WITH current AS (
    SELECT * FROM raw.transfers
    QUALIFY row_number() OVER (PARTITION BY cam_personalpolicyid
                               ORDER BY modifiedon DESC NULLS LAST) = 1
)
SELECT
    t.cam_personalpolicyid                          AS external_id,
    t.ml_tenant_id                                  AS key,
    [t.ml_tenant_id]                                AS lineage,
    try_cast(t.modifiedon AS timestamp)             AS source_updated_at,
    t.cam_name                                      AS name,
    t.cam_status                                    AS status_raw,
    t.cam_transferstatus                            AS transfer_status_raw,
    t.cam_policyholderid_value                      AS policyholder_external_id,
    t.cam_providerid_value                          AS provider_external_id,
    t.cam_financialtransactionid_value              AS financial_transaction_external_id,
    CASE try_cast(t.statecode AS integer) WHEN 0 THEN 'active' WHEN 1 THEN 'inactive' END
                                                    AS status,
    t.statecode                                     AS state_raw,
    try_cast(t.createdon AS timestamp)              AS created_at,
    t.ml_tenant_id,
    current_date                                    AS as_of_date,
    to_json(t)                                      AS data
FROM current t
