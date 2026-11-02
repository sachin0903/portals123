-- A held action asks for a reason only when the firm turns that on.
-- Approval and the reason are separate: approver roles hold the change, and
-- require_justification is the form's "why" box. Off, the form does not ask.

SET LOCAL lock_timeout = '5s';

ALTER TABLE actions.policies
  ADD COLUMN require_justification boolean NOT NULL DEFAULT false;
