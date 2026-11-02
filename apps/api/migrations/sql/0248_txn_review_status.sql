-- The four-eye stage of a Graphene money row, beside the vendor progress on status.
--
-- An action run (actions.runs, status awaiting_approval) is the approval. The book
-- listing reads platform.transactions, which until now stayed pending — the same word
-- as "recorded, not sent" — so a held payment looked like an ordinary unsent one.
-- Null when this send has no approval. Rejected rows that are still open are cancelled
-- by the writer, so they cannot be dispatched later.

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.transactions
  ADD COLUMN review_status text;

ALTER TABLE platform.transactions
  ADD CONSTRAINT transactions_review_status CHECK (
    review_status IS NULL
    OR review_status IN ('awaiting_approval', 'approved', 'rejected')
  );

COMMENT ON COLUMN platform.transactions.review_status IS
  'Four-eye stage of a Graphene money row. Null when this send has no approval. awaiting_approval holds the send. approved means a second person released it. rejected means they refused it and the row is not sent. Vendor progress stays on status.';
