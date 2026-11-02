-- An adviser note on a transfer that arrived from the custodian feed.
-- platform.transfers.note already covers a transfer we raised. A synced row has no
-- transfers row, and the transaction merge must not overwrite this column: it is
-- omitted from book_sink's updatable list for platform.transactions.
SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.transactions
  ADD COLUMN IF NOT EXISTS adviser_note text;

COMMENT ON COLUMN platform.transactions.adviser_note IS
  'Adviser note on a synced transfer. Not a provider field; the merge does not write it.';
