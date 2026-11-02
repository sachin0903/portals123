-- ============================================================================
-- 0051_email_version_authorship.sql — record WHICH SIDE authored an email-template version.
--
-- 0042 gives `email.template_versions` a `created_by` / `published_by` uuid, and the read
-- projection resolves both to a person's NAME (email_templates_service.py::_VERSION_COLS). That is
-- enough to say "Olly Ops changed this" and nothing at all to say "Graphene did". Both the
-- firm-facing router (/email-templates, RLS `authenticated`) and the admin door
-- (/admin/tenants/{id}/email-templates, `service_role`) write through the SAME service, so an
-- operator editing a firm's template on their behalf lands a row that is indistinguishable from
-- one the firm's own staff wrote. A firm must be able to tell.
--
-- Stamped at WRITE time, on the version row — deliberately NOT derived later by joining the author
-- to their tenant. "Who authored this" is a property of the EVENT: a person's tenant membership
-- can be changed or revoked (auth.organisation_memberships is mutable, and an ops operator holds
-- no membership in the firm's tenant at all), so a derivation would silently relabel history the
-- moment the directory moved underneath it.
--
-- NULLABLE, NO DEFAULT — on purpose, twice over:
--   * Rows that predate this migration genuinely have unknown authorship. DEFAULT 'firm' would
--     back-date a claim ("the firm wrote this") that is false for exactly the rows this feature
--     exists to expose — every version the admin door has already written. NULL says "unknown",
--     which is the truth, and the read path surfaces it as null rather than as a side.
--   * No DEFAULT means a call site that FORGETS to declare its side writes NULL (honest silence)
--     rather than the flattering lie. The service makes the parameter mandatory, so mypy is the
--     first line of defence; the absent default is the second.
--
-- The CHECK admits NULL implicitly (a CHECK passes on NULL — unknown is not false), so it
-- constrains the vocabulary without forcing a value onto the legacy rows.
--
-- RLS / GRANTS: nothing to add. Both 0042 policies (`email_template_versions_read` / `_write`)
-- predicate on `tenant_id = ANY (auth.tenant_lineage())` — row-level and column-agnostic — and
-- 0042's grants are whole-table (`GRANT SELECT, INSERT, UPDATE, DELETE ... TO authenticated`), not
-- column-lists, so a new column is covered by both automatically. Concretely: a version authored
-- by operations on a firm's tenant carries that firm's `tenant_id`, so it stays readable BY that
-- firm — the feature must make the row visible-and-labelled, never invisible. The audit spine also
-- needs no change: `audit.record()` snapshots the whole row, so the new column rides along.
--
-- No index: the column is read as a per-row label on an already-narrow, template-scoped history
-- (`email_template_versions_template_idx` serves those queries), never as a filter over the table.
--
-- lock_timeout: ADD COLUMN with no default and no rewrite takes a brief ACCESS EXCLUSIVE lock —
-- fail fast rather than queue behind a long transaction (repo convention, cf. 0030 / 0049).
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- The closed vocabulary. 'firm' = the tenant's own staff, through the firm portal's
-- /email-templates router. 'operations' = Graphene, through the admin door (including the seeding
-- paths: load_defaults and the model-preset clone are operator actions).
ALTER TABLE email.template_versions
    ADD COLUMN authored_by_scope text
        CONSTRAINT email_template_versions_authored_by_scope_chk
        CHECK (authored_by_scope IN ('firm', 'operations'));

COMMENT ON COLUMN email.template_versions.authored_by_scope IS
    'Which SIDE authored this version: ''firm'' (the tenant''s own staff) or ''operations'' '
    '(Graphene, acting on the firm''s behalf). NULL = predates the distinction. Stamped at write '
    'time and STICKY at ''operations'': once Graphene has written a version''s content or '
    'published it, a later firm save inside the same draft bucket must not launder that away.';
