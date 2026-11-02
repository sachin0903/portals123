-- SEI's file states no tax year. The account-details read does, and platform.accounts
-- now stores it when that read supplied the allowance. The previous comment said the
-- column was NULL on every row.

COMMENT ON COLUMN platform.accounts.allowance_tax_year IS
  'Which tax year allowance_used, allowance_limit and allowance_remaining cover. A (client, wrapper) fact, same max()-not-sum() rule as those figures. NULL when the custodian has not stated the year. SEI''s file does not; the account-details read does, and only that read''s figures carry it.';
