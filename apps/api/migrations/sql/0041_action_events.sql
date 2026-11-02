-- ============================================================================
-- 0041_action_events -- audit.actions: the request-level ACTION EVENT stream.
--
-- "Every action taken, across all portals, emits an event." All three portals act
-- through this API, so the API's mutating surface IS the product's action surface:
-- app/actions.py::ActionEventsMiddleware appends one row here for every
-- authenticated mutating request (POST/PUT/PATCH/DELETE) that SUCCEEDS (2xx),
-- best-effort and post-commit. This is the uniform FLOOR under the richer domain
-- mechanisms (the audit.record() DML triggers, documents.audit_log, flows
-- domain_events/outbox), never a replacement for them.
--
-- Designed as a STREAM, not just a trail (the product owner wants to trigger
-- actions off the back of these):
--   * `id` is a monotonic bigint identity -- the consumer cursor. A future worker
--     loop (or a flow trigger) tails `WHERE id > :cursor ORDER BY id` for
--     at-least-once consumption, exactly like the flows outbox drain but read-only.
--   * `event_type` is a stable, machine-routable vocabulary: 'action.' plus the
--     route's OpenAPI operation id ('{tag}-{route name}', main.py's
--     custom_generate_unique_id) -- e.g. 'action.firm-create-contact'. Never free
--     text, so a subscriber can route on it the way the outbox dispatcher routes
--     on its event_type.
--   * the fields a subscriber filters on (tenant, actor, target tenant, target
--     path params) are TOP-LEVEL columns, not buried in metadata.
--
-- Deliberately NOT rows on flows.outbox: the outbox is a *delivery* queue whose
-- dispatcher dead-letters any event_type without a handler (a retry can't fix "no
-- handler"), so enqueuing every request onto it would dead-letter the lot and page
-- an operator via report_error. It is also transactional-with-the-action by design,
-- a guarantee the request-level floor honestly does not have (the middleware writes
-- AFTER the handler's transaction committed). A subscriber that needs transactional
-- enqueue uses a domain event; one that needs "anything happened" tails this stream.
-- ============================================================================

CREATE TABLE audit.actions (
  -- Primary key AND the consumer cursor (monotonic; see the tailing note in
  -- docs/api.md -> "Action events": ids commit slightly out of order, so a strict
  -- tailer reads with a small lag or re-scans).
  id bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,

  -- Machine-routable type: 'action.' || the route's operation id. Stable across
  -- deploys because operation ids are pinned by the generated TS client.
  event_type text NOT NULL,

  -- The acting principal's tenant context (NULL when the principal resolves to no
  -- tenant -- e.g. a break-glass operator not yet mirrored into auth.users).
  tenant_id int REFERENCES auth.tenants(id) ON DELETE CASCADE,
  -- The `?tenant=<uid>` book/ops target, when present: a proxied middle-office
  -- action names BOTH the operator's tenant (tenant_id) and the firm acted on.
  target_tenant_id int REFERENCES auth.tenants(id) ON DELETE CASCADE,

  -- The actor: the WorkOS subject is ALWAYS present (it comes off the verified
  -- token); the auth.users uuid is resolved best-effort via auth.resolve_claims.
  actor_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
  actor_sub text NOT NULL,

  -- The operation: method + the ROUTE TEMPLATE (never the raw path -- templates are
  -- a closed vocabulary; raw paths could smuggle caller-controlled strings into the
  -- trail) + the response status (2xx only, by construction) + the matched path
  -- params (the target ids), redacted through app/actions.py before they land.
  method text NOT NULL,
  route text NOT NULL,
  status int NOT NULL,
  path_params jsonb NOT NULL DEFAULT '{}'::jsonb,

  -- Compact context ONLY (correlation id, redacted query params, break-glass flag).
  -- NEVER request bodies -- the redaction contract lives in app/actions.py and
  -- follows audit.record()'s redact list.
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,

  created_at timestamptz NOT NULL DEFAULT now()
);

-- Cursor-shaped indexes: a subscriber tails the whole stream by id, or one
-- tenant's / one event type's slice of it.
CREATE INDEX actions_tenant_cursor_idx ON audit.actions(tenant_id, id) WHERE tenant_id IS NOT NULL;
CREATE INDEX actions_event_type_cursor_idx ON audit.actions(event_type, id);
CREATE INDEX actions_created_at_idx ON audit.actions(created_at);

-- ---- access ----------------------------------------------------------------
-- service_role ONLY, and append-only even for it (SELECT, INSERT -- no
-- UPDATE/DELETE), the flows.domain_events precedent: this is evidence and a
-- replayable stream, so nothing rewrites it. No `authenticated` grant or policy at
-- all -- withholding the privilege beats an always-false policy (the
-- aml.operator_secrets precedent). When a tenant-facing read surface is wanted it
-- ships as an operations endpoint gated on `audit:read` (service_role, hand-scoped),
-- or this gains a lineage-scoped SELECT policy + grant in its own migration.
ALTER TABLE audit.actions ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT ON audit.actions TO service_role;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA audit TO service_role;
ALTER TABLE audit.actions FORCE ROW LEVEL SECURITY;

-- NO audit.attach here, on purpose: this table IS the audit plane -- triggering
-- audit.record() on it would write a second audit.log row per action event.
