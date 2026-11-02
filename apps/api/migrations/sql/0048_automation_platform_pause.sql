-- ============================================================================
-- 0048_automation_platform_pause.sql — the estate-wide automation kill switch.
--
-- `automations:global_pause` has existed since 0043 as a registered, contract-mirrored,
-- operations-portal permission with NOTHING behind it: no endpoint, no flag, no consumer check.
-- A permission that exists to be granted but is never checked is worse than an absent one — it
-- reads, to whoever grants it, as a control the platform has. This is the control.
--
-- Deliberately NOT a column on `flows.automation_settings`. That table is per-tenant and a firm
-- writes its own row (`automations:pause`); folding the platform switch into it would mean either
-- Graphene writing every firm's row (and losing which firms had paused THEMSELVES the moment it
-- resumed) or a firm's own PUT touching a column only Graphene may set. One row, one owner.
--
-- `resume_from_event_id` is the part that makes "pause" mean pause rather than "hold and then
-- flood". Firings skipped while a single tenant is paused are DROPPED (recorded as a
-- `skipped_paused` delivery) — that is the established semantics, and an operator who kills the
-- automation plane at 03:00 because a rule is emailing clients must not have four hours of held
-- emails released when they resume. The consumer's cursor is therefore parked at the stream head
-- on resume, exactly as `automation._ensure_cursor` parks a fresh consumer at the head for the
-- same reason. Storing the id (rather than doing it in-process on the transition) makes it
-- survive a worker restart mid-pause.
--
-- Expand-only: one new table, nothing altered. Its downgrade drops it and the platform is back to
-- the state this migration exists to end.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

CREATE TABLE flows.automation_platform_pause (
  -- The singleton. A boolean PK fixed at true by the CHECK is the house way of saying "there is
  -- exactly one of these" without an id nobody uses: a second INSERT collides on the key.
  id boolean PRIMARY KEY DEFAULT true CHECK (id),

  paused boolean NOT NULL DEFAULT false,
  -- Why. Required by the endpoint when pausing, not by the column: an incident switch with no
  -- note is the one the next operator daren't touch.
  reason text,

  -- The `audit.actions` id the consumer must skip past when it next runs. Set when a resume
  -- lands; cleared by the worker once it has parked the cursor there.
  resume_from_event_id bigint,

  paused_at timestamptz,
  paused_by uuid REFERENCES auth.users(id) ON DELETE SET NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Seeded, unlike 0043's cursor row: this one has a meaningful default state ("not paused") that
-- every reader wants to see, and the read is on the worker's loop — a missing row would make the
-- common path an INSERT race between the worker and the endpoint.
INSERT INTO flows.automation_platform_pause (id, paused) VALUES (true, false);

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================
-- Estate-wide state with no tenant_id, so `auth.tenant_lineage()` has nothing to say about it.
-- Reads and writes go through the operations-surface endpoint on a service_role session, gated on
-- `automations:global_pause` — so, like `flows.automation_cursor` and `aml.operator_secrets`, the
-- table simply has NO `authenticated` grant and NO policy. Withholding the privilege beats an
-- always-false policy: there is no claim shape that could satisfy one.
ALTER TABLE flows.automation_platform_pause ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE ON flows.automation_platform_pause TO service_role;

CREATE TRIGGER set_updated_at BEFORE UPDATE ON flows.automation_platform_pause
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Audited for the same reason 0043 attaches `automation_settings`: "who turned the safety off,
-- and when did they turn it back on" is the first question after any incident. Low-volume by
-- construction — a human presses this, not the consumer.
SELECT audit.attach(ARRAY['flows.automation_platform_pause']);

ALTER TABLE flows.automation_platform_pause FORCE ROW LEVEL SECURITY;
