-- ============================================================================
-- 0202_sipp_scheme_admin — Morgan Lloyd maps SIPP, not TPPENSION.
--
-- `providers.attach_scheme_administrators` used `is_pension OR product_type IN
-- (SIPP, PENSION, SSAS)`. `is_pension` is the DISPLAY flag (0141) and is true
-- for TPPENSION and SSAS, so enabling Morgan Lloyd tagged every pension wrapper
-- as scheme administrator. Drawdown and the products page then named Morgan
-- Lloyd on a third-party pension it does not run.
--
-- The writer now inserts SIPP only and drops the rest on the next attach. This
-- migration is the one-shot for shelves that will not be saved again.
--
-- As service_role: platform.product_platforms is FORCE RLS and the migrator is
-- not BYPASSRLS (the 0059 trap). A DELETE without the role reports success and
-- removes nothing.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE service_role;

DELETE FROM platform.product_platforms pp
 USING platform.products p
 WHERE pp.product_id = p.id
   AND pp.provider = 'morgan-lloyd'
   AND upper(p.product_type::text) <> 'SIPP';

RESET ROLE;
