-- 0157_gr_identifier_english — UAT-8: Greece's tax-identifier localeName was Greek.
-- FORCE RLS: the migrator sees no rows unless it runs as service_role.

SET LOCAL ROLE service_role;

UPDATE ref.countries
SET identifiers = jsonb_set(
  jsonb_set(
    identifiers,
    '{0,localeName}',
    to_jsonb(identifiers->0->>'name')
  ),
  '{1,localeName}',
  to_jsonb(coalesce(identifiers->1->>'name', identifiers->1->>'localeName'))
)
WHERE code = 'GR';

RESET ROLE;
