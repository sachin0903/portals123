-- 0117_comms_delayed -- `delayed` is a real delivery state, not a silent one.
--
-- 0111 mapped Resend's `email.delivery_delayed` to no status change, on the reasoning that a delay
-- is progress rather than a state: the mail is still in flight and calling it anything else would
-- be a guess.
--
-- That was wrong, and a real mailbox showed it. A delayed message can sit retrying for HOURS —
-- every send to a domain that is slow, greylisting, or simply does not exist ends up here — and
-- under 0111 the feed reported all of them as `sent`, identical to a message handed over one second
-- ago. "It has not arrived and we are still trying" is exactly the thing a firm chasing a client
-- needs to see, and it was the one outcome the ledger could not express.
--
-- Ranked BETWEEN sent and delivered (app/comms/ledger.py::STATUS_RANK), so:
--   * a delay after a send advances the row (sent -> delayed);
--   * a delivery still supersedes it (delayed -> delivered), which is the common ending — a delayed
--     message usually does arrive;
--   * a late `email.sent` callback cannot walk a delayed row backwards, as before;
--   * a bounce still wins from anywhere, which is the other ending.

-- The stamp for the new state. Nullable like every other one: a message that was never
-- delayed simply has none, and the timeline shows only the stamps that exist.
ALTER TABLE comms.messages ADD COLUMN IF NOT EXISTS delayed_at timestamptz;

ALTER TABLE comms.messages DROP CONSTRAINT IF EXISTS messages_status_check;
-- No 'opened'/'clicked': this product does not do read receipts (0111's own note says why, and
-- app/comms/ledger.py::NOT_TRACKED drops those callbacks before anything is written).
ALTER TABLE comms.messages ADD CONSTRAINT messages_status_check CHECK (status IN (
  'unconfigured', 'suppressed', 'failed', 'sent', 'delayed', 'delivered',
  'bounced', 'complained'));
