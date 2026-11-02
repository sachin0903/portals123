-- l10n.terms: the `instrument` term's default label becomes "Instrument", not "Asset".
--
-- 0042 seeded it as Asset/Assets with the note "'Asset' and 'instrument' are the usual
-- disagreement" — and then the platform picked a side everywhere else. `platform.instruments` is
-- the canonical relation in both the lake (docs/platform-model.md §3) and OLTP, the materialiser
-- reads `instruments`, and the admin catalogue page is now Instruments too. Leaving the DEFAULT
-- label as "Asset" meant every portal rendered the provider's word for the thing the rest of the
-- system calls an instrument. "Asset" is what Seccl names its `/asset` endpoint and what
-- `raw.assets` lands; it is a provider dialect, not our vocabulary.
--
-- OVERRIDES ARE UNTOUCHED, and that is the whole point of the split: a firm that genuinely says
-- "Assets" has a row in `l10n.overrides`, and this migration must not overrule it. Only the
-- fallback moves — so a firm that has expressed no preference now reads "Instruments", and a firm
-- that has expressed one keeps it. (A firm that wants "Assets" and has no override can set one in
-- Branding → Terminology; before this change it got that wording by accident rather than choice.)
--
-- As service_role: l10n.terms has FORCE RLS with a SELECT-only policy, so the migrator role
-- cannot write its own seed — the same trap 0042's own INSERT documents.
SET LOCAL ROLE service_role;

UPDATE l10n.terms
   SET default_label = 'Instrument',
       default_plural = 'Instruments',
       description = 'The security held. "Asset" is the provider dialect (Seccl''s /asset '
                     'endpoint); the canonical relation is platform.instruments.'
 WHERE key = 'instrument'
   -- Idempotent, and it refuses to rewrite a label someone has since changed by hand: only the
   -- exact 0042 seed value is moved on.
   AND default_label = 'Asset';

RESET ROLE;
