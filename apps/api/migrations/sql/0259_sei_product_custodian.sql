-- ============================================================================
-- 0259_sei_product_custodian — SEI on the shelf is a custodian, and an account
-- with nowhere else to go takes it.
--
-- Enabling SEI wrote tenant_platforms and nothing on the product. The product
-- list and the journey both require product_platforms.role = 'custodian'. A
-- template save inserted the row with a null role, and the nightly merge only
-- updates external_id, so the null stayed. The account was then opened with
-- no custodian and every action skipped.
--
-- Fill a null role from the provider's own role. Attach SEI as custodian to
-- GIA, ISA, JISA and SIPP where the firm has SEI enabled and the row is
-- missing. Leave an existing external id, enrolment template and enabled flag
-- alone. Then, where an account still has no custodian:
--   * one primary custodian link wins;
--   * otherwise exactly one enabled custodian on the product wins.
-- An account that already names a custodian, or whose product names two, is
-- left as it is. A third-party bond or pension is not attached.
--
-- As service_role: platform.product_platforms and platform.accounts are FORCE
-- RLS and the migrator is not BYPASSRLS. An UPDATE without the role reports
-- success and changes nothing.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE service_role;

UPDATE platform.product_platforms
   SET role = CASE provider
                WHEN 'morgan-lloyd' THEN 'scheme_administrator'
                WHEN 'seccl'        THEN 'custodian'
                WHEN 'sei'          THEN 'custodian'
              END
 WHERE role IS NULL
   AND provider IN ('morgan-lloyd', 'seccl', 'sei');

INSERT INTO platform.product_platforms
  (tenant_id, product_id, provider, role, enabled)
SELECT p.tenant_id, p.id, 'sei'::platform.provider_code, 'custodian', true
FROM platform.products p
JOIN platform.tenant_platforms tp
  ON tp.tenant_id = p.tenant_id
 AND tp.provider = 'sei'
 AND tp.enabled
WHERE upper(p.product_type::text) IN ('GIA', 'ISA', 'JISA', 'SIPP')
ON CONFLICT (product_id, provider) DO UPDATE SET
  role = coalesce(platform.product_platforms.role, EXCLUDED.role)
WHERE platform.product_platforms.role IS NULL;

UPDATE platform.accounts a
   SET custodian = l.provider
  FROM platform.links l
 WHERE a.custodian IS NULL
   AND l.tenant_id = a.tenant_id
   AND l.entity_type = 'account'
   AND l.entity_id = a.id
   AND l.link_type = 'primary'
   AND l.provider IN ('sei', 'seccl')
   AND (
     SELECT count(DISTINCT l2.provider)
     FROM platform.links l2
     WHERE l2.tenant_id = a.tenant_id
       AND l2.entity_type = 'account'
       AND l2.entity_id = a.id
       AND l2.link_type = 'primary'
       AND l2.provider IN ('sei', 'seccl')
   ) = 1;

UPDATE platform.accounts a
   SET custodian = chosen.provider
  FROM (
    SELECT a2.id, (array_agg(pp.provider))[1] AS provider
    FROM platform.accounts a2
    JOIN platform.product_platforms pp
      ON pp.product_id = a2.product_id
     AND pp.tenant_id = a2.tenant_id
     AND pp.enabled
     AND pp.role = 'custodian'
    JOIN platform.tenant_platforms tp
      ON tp.tenant_id = a2.tenant_id
     AND tp.provider = pp.provider
     AND tp.enabled
    WHERE a2.custodian IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM platform.links l
        WHERE l.tenant_id = a2.tenant_id
          AND l.entity_type = 'account'
          AND l.entity_id = a2.id
          AND l.link_type = 'primary'
          AND l.provider IN ('sei', 'seccl')
      )
    GROUP BY a2.id
    HAVING count(DISTINCT pp.provider) = 1
  ) chosen
 WHERE a.id = chosen.id;

RESET ROLE;
