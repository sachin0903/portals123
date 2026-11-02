-- ============================================================================
-- 0271_employer_bank_account -- the employer's bank account gets its own column on the client
-- record, and the fact-find rows written into `data` move onto it.
--
-- The fact-find's employer step asks for the scheme's sort code and account number. 0210 moved
-- every other employer answer onto the Employer card's columns and left these two under
-- `data.pension.employerContributions.{sortCode, accountNumber}`, where the record listed them
-- as "2 more from the fact-find": two digit strings with no heading, no editor and no sort-code
-- directory check. The column is the same jsonb shape as `bank_account`, so the one normaliser,
-- the one directory check and the one register serve both.
--
-- Backfill: a 6-digit sort code is stored as `12-34-56` and a 7-digit account number is left-
-- padded to 8, the Bacs convention `normalise_bank_account` writes. Anything that is not a UK
-- pair stays in `data` for a person to look at. The keys the column replaces are REMOVED from
-- `data` (the record shows whatever is left in `data` as gathered overflow), and an
-- `employerContributions` object left empty by that removal goes too.
--
-- FORCE RLS on crm.contacts makes an UPDATE from the migrator a no-op that reports success
-- (gotchas.md); service_role is the same lift 0210 takes.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

ALTER TABLE crm.contacts ADD COLUMN IF NOT EXISTS employer_bank_account jsonb;

COMMENT ON COLUMN crm.contacts.employer_bank_account IS
  'The employer''s bank account, the scheme paying employer contributions in: the same shape as '
  'bank_account ({name, bank, sort_code, account_number}), written through the same normaliser '
  'and sort-code directory check, shown on the Employer card. 0271.';

SET LOCAL ROLE service_role;

WITH held AS (
  SELECT c.id,
         regexp_replace(COALESCE(c.data -> 'pension' -> 'employerContributions' ->> 'sortCode', ''),
                        '\D', '', 'g') AS sort_digits,
         regexp_replace(COALESCE(c.data -> 'pension' -> 'employerContributions' ->> 'accountNumber', ''),
                        '\D', '', 'g') AS number_digits
  FROM crm.contacts c
  WHERE c.employer_bank_account IS NULL
    AND jsonb_typeof(c.data -> 'pension' -> 'employerContributions') = 'object'
    AND (c.data -> 'pension' -> 'employerContributions' ? 'sortCode'
         OR c.data -> 'pension' -> 'employerContributions' ? 'accountNumber')
),
pairs AS (
  SELECT id,
         substr(sort_digits, 1, 2) || '-' || substr(sort_digits, 3, 2) || '-' || substr(sort_digits, 5, 2)
           AS sort_code,
         lpad(number_digits, 8, '0') AS account_number
  FROM held
  WHERE length(sort_digits) = 6 AND length(number_digits) IN (7, 8)
)
UPDATE crm.contacts AS c
   SET employer_bank_account = jsonb_build_object(
         'sort_code', p.sort_code,
         'account_number', p.account_number
       ),
       data = CASE
                -- Parentheses are load-bearing: `-` binds tighter than `->` (0210).
                WHEN jsonb_typeof(((c.data -> 'pension') -> 'employerContributions')
                                  - 'sortCode' - 'accountNumber') = 'object'
                 AND (((c.data -> 'pension') -> 'employerContributions') - 'sortCode' - 'accountNumber') = '{}'::jsonb
                THEN CASE
                       WHEN ((c.data -> 'pension') - 'employerContributions') = '{}'::jsonb
                       THEN c.data - 'pension'
                       ELSE c.data || jsonb_build_object('pension', (c.data -> 'pension') - 'employerContributions')
                     END
                ELSE c.data || jsonb_build_object(
                       'pension',
                       (c.data -> 'pension') || jsonb_build_object(
                         'employerContributions',
                         ((c.data -> 'pension') -> 'employerContributions') - 'sortCode' - 'accountNumber'
                       )
                     )
              END
  FROM pairs p
 WHERE p.id = c.id;

RESET ROLE;
