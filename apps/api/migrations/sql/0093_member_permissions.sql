-- auth.member_permissions -- a role set's current permission slugs, from the DB tables that are
-- the actual source of truth (auth.role_permissions -> auth.permissions), rather than trusting
-- whatever WorkOS happened to embed in the access token's `permissions` claim.
--
-- Deliberately a NEW function, not an extra key on auth.resolve_claims's existing return value:
-- every one of db.py's session functions does
--   claims = {**build_jwt_claims(principal), **resolved}
-- with `resolved` (auth.resolve_claims's return) spread SECOND, so a `permissions` key on that
-- jsonb would silently override build_jwt_claims's `principal.permissions` -- including the
-- SYSTEM_USER_IDS break-glass grant `_apply_break_glass` adds in Python. That collision would
-- have silently dropped `auth.is_system()` for every break-glass request. Kept separate, this
-- function is used by exactly one caller (app/auth.py::_permissions_for), before
-- `principal.permissions` is otherwise decided, so nothing downstream can be shadowed by it.
--
-- Takes `role_ids` directly rather than a WorkOS identity (and calling auth.resolve_claims
-- itself, as an earlier version of this migration did): app/auth.py::resolve_claims_cached
-- caches resolve_claims's result on the request for the session dependency that runs right after
-- this one to reuse (tenant_session/firm_session/admin_session/book_session all resolve the SAME
-- identity moments later) -- bundling resolve_claims into this function would have made that
-- result opaque to Python and impossible to cache, so every request would run the expensive
-- SECURITY DEFINER resolve (lineage aggregation, portal classification, contact/account scope)
-- TWICE, on two separate connections, for identical inputs.
CREATE OR REPLACE FUNCTION auth.member_permissions(p_role_ids int[])
  RETURNS text[] LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = 'auth' AS $fn$
DECLARE
  v_permissions text[];
BEGIN
  IF p_role_ids IS NULL OR array_length(p_role_ids, 1) IS NULL THEN
    RETURN '{}'::text[];
  END IF;

  SELECT COALESCE(array_agg(DISTINCT p.slug), '{}'::text[]) INTO v_permissions
    FROM auth.role_permissions rp
    JOIN auth.permissions p ON p.id = rp.permission_id
    WHERE rp.role_id = ANY(p_role_ids);

  RETURN v_permissions;
END;
$fn$;

ALTER FUNCTION auth.member_permissions(int[]) OWNER TO service_role;
GRANT EXECUTE ON FUNCTION auth.member_permissions(int[]) TO authenticated, service_role;

COMMENT ON FUNCTION auth.member_permissions(int[]) IS
  'DB-sourced replacement for trusting the JWT permissions claim (SECURITY DEFINER, '
  'service_role/BYPASSRLS -- no request claims exist yet when app/auth.py calls this). Called '
  'once per request by _permissions_for, given the role_ids auth.resolve_claims already '
  'resolved (and cached, for db.py''s session dependencies to reuse) -- before break-glass is '
  'applied.';
