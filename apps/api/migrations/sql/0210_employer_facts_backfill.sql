-- ============================================================================
-- 0210_employer_facts_backfill -- move the fact-find's employer answers out of `data` and into
-- the columns the client record reads.
--
-- 0139 gave the Employer card its own columns (`employer_contributions`, `employer_name`,
-- `employer_registration_number`, `employer_address`) and 0116 added `source_of_wealth_detail`.
-- The onboarding fact-find kept mapping the same answers to where the legacy app had them:
-- `data.pension.hasEmployerContributions`, `data.pension.employerContributions.{name,
-- registrationNumber}` and a top-level `data.source_of_wealth_detail`. So a client whose adviser
-- had answered every employer question showed three em dashes on the card and
-- "HasEmployerContributions: Yes" under "2 more from the fact-find".
--
-- The seed's mapping now targets the columns. This is the rows written before it did. The column
-- wins where both hold a value -- an adviser who typed on the record after onboarding said what
-- they meant -- and the keys the columns replace are REMOVED from `data`, because the record shows
-- whatever is left in `data` as gathered overflow and would otherwise state each fact twice. The
-- employer's bank details (`accountNumber`, `sortCode`) stay: no column holds them.
--
-- FORCE RLS on crm.contacts makes an UPDATE from the migrator a no-op that reports success
-- (gotchas.md); service_role is the same lift 0059 / 0153 / 0164 take.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE service_role;

UPDATE crm.contacts AS c
   SET employer_contributions = COALESCE(
         c.employer_contributions,
         CASE lower(c.data -> 'pension' ->> 'hasEmployerContributions')
           WHEN 'true' THEN true
           WHEN 'false' THEN false
           ELSE NULL
         END
       ),
       employer_name = COALESCE(
         c.employer_name,
         nullif(btrim(c.data -> 'pension' -> 'employerContributions' ->> 'name'), '')
       ),
       employer_registration_number = COALESCE(
         c.employer_registration_number,
         nullif(btrim(c.data -> 'pension' -> 'employerContributions' ->> 'registrationNumber'), '')
       ),
       source_of_wealth_detail = COALESCE(
         c.source_of_wealth_detail,
         nullif(btrim(c.data ->> 'source_of_wealth_detail'), '')
       ),
       data = (
         (c.data - 'source_of_wealth_detail')
         || CASE
              WHEN c.data ? 'pension' AND jsonb_typeof(c.data -> 'pension') = 'object' THEN
                jsonb_build_object(
                  'pension',
                  -- Parentheses are load-bearing. `-` binds tighter than `->`, so
                  -- `data -> 'pension' - 'key'` parses as a subtraction of two unknown
                  -- strings and the migration dies with "operator is not unique".
                  ((c.data -> 'pension') - 'hasEmployerContributions')
                  || CASE
                       WHEN jsonb_typeof((c.data -> 'pension') -> 'employerContributions') = 'object'
                       THEN jsonb_build_object(
                              'employerContributions',
                              (((c.data -> 'pension') -> 'employerContributions')
                                - 'name') - 'registrationNumber'
                            )
                       ELSE '{}'::jsonb
                     END
                )
              ELSE '{}'::jsonb
            END
       )
 WHERE c.data IS NOT NULL
   AND (
        c.data ? 'source_of_wealth_detail'
     OR c.data -> 'pension' ? 'hasEmployerContributions'
     OR c.data -> 'pension' -> 'employerContributions' ? 'name'
     OR c.data -> 'pension' -> 'employerContributions' ? 'registrationNumber'
   );

-- An `employerContributions` object left with nothing in it, or a `pension` object left with
-- nothing in it, is a key the overflow would still have to walk. Drop the empties.
UPDATE crm.contacts AS c
   SET data = CASE
         WHEN (c.data -> 'pension') - 'employerContributions' = '{}'::jsonb
              AND COALESCE(c.data -> 'pension' -> 'employerContributions', '{}'::jsonb) = '{}'::jsonb
         THEN c.data - 'pension'
         WHEN c.data -> 'pension' -> 'employerContributions' = '{}'::jsonb
         THEN jsonb_set(c.data, '{pension}', (c.data -> 'pension') - 'employerContributions')
         ELSE c.data
       END
 WHERE c.data IS NOT NULL
   AND jsonb_typeof(c.data -> 'pension') = 'object'
   AND (
        c.data -> 'pension' -> 'employerContributions' = '{}'::jsonb
     OR (c.data -> 'pension') - 'employerContributions' = '{}'::jsonb
   );

RESET ROLE;
