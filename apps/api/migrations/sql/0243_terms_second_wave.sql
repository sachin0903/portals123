-- l10n.terms: six more nouns firms rename, chosen from what the portals actually render.
--
-- 0042 seeded seventeen from a reading of what firms disagree about. This wave is chosen the other
-- way round -- by counting the nouns the portals put on screen and asking which of them a firm
-- would plausibly call something else. Every key here is one a surface already renders, so an
-- override has somewhere to land; that was 0042's own rule and it is the one that stops this table
-- filling with words nobody reads.
--
--   document      12 render sites. "Files", "paperwork", "correspondence" are all in use.
--   note           8. "File note" and "case note" are the common variants.
--   task           2, and the platform ALSO calls them "action items" in its own copy -- so this
--                  key settles an inconsistency of ours before it settles a firm's preference.
--   illustration   The client-facing name varies most of any document we produce: "projection",
--                  "KFI", "quote", "key features illustration".
--   journey        Already our own rename of "flow" (docs/flows.md). Firms say "process" or
--                  "workflow", and the word appears in nav, listings and the builder.
--   beneficiary    Collected at onboarding and pushed to Morgan Lloyd. "Nominee" and "dependant"
--                  are both used, and they are not synonyms in every firm's mouth -- which is
--                  precisely why it should be the firm's word rather than ours.
--
-- Deliberately NOT added:
--   provider / wrapper  -- these are SYNONYMS of `platform` and `product`, which 0042 already
--                          catalogues (its own descriptions say so: "some firms say 'provider'",
--                          "the wrapper offered to clients"). A second key for the same concept
--                          would let one firm set two different words for one thing.
--   screening / kyc     -- Graphene's own regulatory review, not the firm's process. The firm sees
--                          an outcome, never the review (docs/aml.md), and renaming ours in their
--                          portal would misdescribe whose activity it is.
--   fee                 -- `fee_package` is the configurable noun; a bare "fee" is an amount on a
--                          row, not an entity a firm names.
--
-- As service_role: l10n.terms has FORCE RLS with a SELECT-only policy, so the migrator role cannot
-- write its own seed -- the trap 0042's INSERT documents and 0058 hit again.
SET LOCAL ROLE service_role;

INSERT INTO l10n.terms (key, category, default_label, default_plural, description, sort_order) VALUES
  ('document',     'records', 'Document',     'Documents',     'Anything filed against a client. Some firms say "files" or "correspondence".', 180),
  ('note',         'records', 'Note',         'Notes',         'A record of contact or a decision. Often "file note" or "case note".', 190),
  ('task',         'records', 'Task',         'Tasks',         'An action item assigned to staff or a client. Some firms say "action" or "to-do".', 200),
  ('illustration', 'advice',  'Illustration', 'Illustrations', 'A projection of what a pension may be worth. Also "projection", "KFI" or "quote".', 210),
  ('journey',      'advice',  'Journey',      'Journeys',      'A multi-step process a client is taken through. Some firms say "process" or "workflow".', 220),
  ('beneficiary',  'people',  'Beneficiary',  'Beneficiaries', 'Who receives the fund on death. Some firms say "nominee" or "dependant".', 230)
ON CONFLICT (key) DO NOTHING;

RESET ROLE;
