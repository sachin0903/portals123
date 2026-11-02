-- ============================================================================
-- 0278_intelliflo_plan_unique -- one Intelliflo plan is linked to one account, across ALL tenants.
--
-- The link write checked this with a SELECT scoped to the caller's own tenant and then INSERTed, with
-- nothing in the database behind it. Two things got through: a race between two requests, and, since
-- every portals tenant reaches the SAME Intelliflo tenant (one set of API credentials), two portals
-- tenants linking the same plan. Both firms' valuations and transactions then land on that plan, and
-- its policy number can only be right for one of them.
--
-- The index is deliberately NOT keyed by tenant_id, and the link write turns a violation into a 409
-- that names nothing about the other tenant's account.
--
-- A database that already holds a plan linked twice fails this migration, on purpose, listing the
-- plans: which account keeps the link is an operator's call, not one to make in DDL.
--
-- THE ENUM VALUE IS NEVER WRITTEN AS A LITERAL HERE. `intelliflo` joined `platform.provider_code` in
-- 0249 with ADD VALUE, and a fresh database runs every migration in ONE transaction, so naming the
-- new member again (`provider = 'intelliflo'`) fails with "unsafe use of new value" (0168's trap).
-- The comparison goes through the text of the column instead, and the index predicate through a
-- tiny IMMUTABLE wrapper, because an index predicate may not call the (merely STABLE) enum cast.
-- An enum's labels never change once added, so the wrapper is honest.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

DO $$
DECLARE
    dupes text;
BEGIN
    SELECT string_agg(format('%s (%s accounts)', plan, n), ', ') INTO dupes
    FROM (
        SELECT data ->> 'id' AS plan, count(*) AS n
        FROM platform.links
        WHERE provider::text = 'intelliflo' AND entity_type = 'account' AND link_type = 'primary'
          AND data ? 'id'
        GROUP BY 1 HAVING count(*) > 1
    ) d;
    IF dupes IS NOT NULL THEN
        RAISE EXCEPTION 'Intelliflo plans linked to more than one account: %. Unlink all but one, then re-run.', dupes;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION platform.provider_code_text(platform.provider_code) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE AS 'SELECT $1::text';

CREATE UNIQUE INDEX IF NOT EXISTS links_intelliflo_plan_uq
    ON platform.links ((data ->> 'id'))
    WHERE platform.provider_code_text(provider) = 'intelliflo'
      AND entity_type = 'account' AND link_type = 'primary' AND data ? 'id';
