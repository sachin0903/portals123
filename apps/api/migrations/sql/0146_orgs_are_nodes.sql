-- ============================================================================
-- 0146_orgs_are_nodes -- `platform.orgs` is a table of NODES, and now says so.
--
-- ---------------------------------------------------------------------------
-- IT ALREADY WAS ONE, in the sense that matters: every entity is scoped by ONE containment test
-- over the ancestry, whatever kind of unit each row happens to be.
-- ---------------------------------------------------------------------------
-- Seccl's own `nodeType` values are `Default` and `Client Bank`, which its conformed model maps to
-- the `node` fallback, so the provider's word survives only in `type_raw`; ML's org axis is the
-- single Microsoft tenant. 50 of 50 rows on the dev book are `node`.
--
-- **THE ENUM KEEPS ALL FIVE MEMBERS, and that is a correction rather than caution.** "Only `node`
-- is used" is true of THIS DEPLOYMENT'S DATA and false of the code:
-- `services/sei/models/platform/nodes.sql` publishes `'firm'`, `'subfirm'` and `'team'` as
-- literals, one per branch of its three-level hierarchy. Narrowing the type would have rejected
-- SEI's entire org dimension on the first run against a firm that has one -- and an unresolvable
-- parent takes every entity below it with it, which lands as "this firm has no data with this
-- custodian". The dev book holds no SEI data, which is exactly why the row counts looked
-- conclusive and were not.
--
-- This is a straight revert of the VOCABULARY half of commit 31870ca1, which deleted a
-- `services/seccl/models/platform/nodes.sql` that already published a column called `lineage` --
-- and whose successor says so in its own header: "Supersedes platform.nodes. The recursion is
-- unchanged -- what changes is the vocabulary: `lineage` becomes `org_path` and `id` becomes both
-- `external_id` and `org_key`." The recursion is still unchanged. The words go back.
--
-- ---------------------------------------------------------------------------
-- WHAT `lineage` HAS TO KEEP, because four things depend on it and none is obvious.
-- ---------------------------------------------------------------------------
--   1. ROOT->LEAF ORDER, not a set: the admin node picker derives its indent from
--      `len(lineage) - 1` and sorts on a zero-padded transform to get a pre-order tree walk.
--   2. SELF-INCLUSIVE: a node's own key is the last element, so `list_has_any` matches the node
--      as well as its descendants.
--   3. EMPTY MEANS GLOBAL. `len(lineage) = 0` is the projects-to-every-tenant sentinel for
--      reference data (an instrument is not owned by a firm, and neither is a firm-wide fee
--      register). Publishing the firm ROOT's path instead looks tighter and is wrong -- it is
--      what made `platform.third_party_providers` project 0 of 7 rows, because containment tests
--      ANCESTRY and a root-owned row matches only a tenant holding the root.
--      AND THE CONVERSE: an UNRESOLVABLE owner publishes NULL, never `[]`. Twenty-one conformed
--      models spelt "unknown" as "global" (`coalesce(o.lineage, [])`, or a `list_filter` over
--      all-NULL inputs), so one bad node id would have published an account, its positions, its
--      transactions and its valuations into every enabled tenant's slice.
--      `pkgs/book-sink/tests/test_lineage_sentinel.py` refuses that spelling now.
--   4. `VARCHAR[]`, NEVER a bare NULL -- which types as INTEGER in DuckDB and makes the join
--      silently never match. A typed NULL is the "unknown" of (3); a BARE one is the type bug.
--
-- `lineage` sits one line from `auth.tenant_lineage()` in this table's own RLS policy, and they
-- are DIFFERENT ANCESTRIES: one is the firm tree inside a custodian, the other is the tenant tree
-- inside Graphene. Same word, same shape, unrelated hierarchies — stated here because the policy
-- below reads `tenant_id = ANY (auth.tenant_lineage())` on a table whose own column is `lineage`.
--
-- ---------------------------------------------------------------------------
-- WHAT THE RENAME DOES NOT REACH.
-- ---------------------------------------------------------------------------
-- `"orgs"` is also the persisted `entity` value in `platform.merge_runs` and
-- `platform.merge_rejects`. Those are append-only ledgers of what happened, so historical rows
-- keep saying `orgs` for ever and are NOT rewritten -- the same rule `audit.actions` sets. New
-- rows say `nodes`, and a reader spanning the change sees both, which is the truth about it.
--
-- ---------------------------------------------------------------------------
-- OPERATIONAL: THE TWO IMAGES HAVE TO BE CUT TOGETHER. There is no shim, deliberately.
-- ---------------------------------------------------------------------------
-- A rename is definitionally not backwards-compatible, and the root CLAUDE.md's Conventions
-- section says migrations are expand/contract. That rule has been bent here before (0020 and 0048
-- `RENAME COLUMN`; 0043, 0048, 0094 `RENAME TO`, none leaving a shim either) and the reason it is
-- acceptable is WHICH reader is stale, which is worth writing down because it is not the obvious
-- one:
--
--   * NO API ROUTER SELECTS `accounts.org_id` or `contacts.org_id`. The app reads the book through
--     projections that never named the column, so the running API is unaffected by the rename and
--     the deploy's ordering (migrate, then release) is safe for it exactly as `deploy.yml` says.
--   * THE STALE READER IS THE PIPELINE IMAGE. `pkgs/book-sink/src/book_sink/entities.py` names
--     these columns directly -- on main, `Parent(column="org_id", source="org_key", via="org")` --
--     so a `book-sink`/provider container still running the PREVIOUS image against the renamed
--     schema fails its merge for the whole tenant, and a failed merge lands in
--     `platform.merge_rejects` as "unavailable", which reads as "this firm has no data with this
--     custodian" rather than as a deploy that has not finished.
--
-- `deploy.yml` builds and pushes EVERY deployable image before it migrates and only then triggers
-- the release, so a normal deploy is already in the right order; the exposure is the window
-- between the migration and the pipeline containers actually restarting, and a chain that fires on
-- its cron inside that window. A shim would have to be a writable view or a rule over four renamed
-- columns for one release, to protect a nightly job that retries -- so the answer is the ordering
-- plus this note, not machinery. If a provider chain is mid-run, let it finish or expect one
-- night's rejects.
-- ============================================================================

ALTER TABLE platform.orgs RENAME TO nodes;

ALTER TABLE platform.nodes RENAME COLUMN org_key TO key;
ALTER TABLE platform.nodes RENAME COLUMN org_path TO lineage;
ALTER TABLE platform.nodes RENAME COLUMN org_type TO type;

-- Written by nothing, read by nothing, and `false` on every row: it appears exactly ONCE in the
-- whole repository, on its own DDL line in 0041. Its comment claimed it made "which org am I?"
-- answerable without re-deriving the predicate; nothing ever asked.
ALTER TABLE platform.nodes DROP COLUMN is_tenant_root;

-- The type is RENAMED, not narrowed. Postgres cannot rename an enum's members and cannot drop
-- one, so a rename of the type itself is the whole change -- and there is nothing to drop:
-- `firm`, `subfirm` and `team` are published as literals by SEI's own conformed model.
ALTER TYPE platform.org_type RENAME TO node_type;

COMMENT ON TYPE platform.node_type IS
  'What kind of org unit a provider published. Seccl maps its own vocabulary onto `node` and '
  'keeps the raw word in `type_raw`; SEI publishes `firm`, `subfirm` and `team`, one per level of '
  'its hierarchy. Every entity is scoped by one containment test over `lineage` regardless, so '
  'this is descriptive rather than something the predicate reads.';

-- The FK columns follow the table.
ALTER TABLE platform.accounts RENAME COLUMN org_id TO node_id;
ALTER TABLE crm.contacts     RENAME COLUMN org_id TO node_id;

-- Constraints and indexes follow too. Not required for correctness -- required for anyone reading
-- the schema to find them, which is the reason 0043 gives for doing the same thing.
ALTER TABLE platform.nodes RENAME CONSTRAINT orgs_unique TO nodes_unique;
ALTER INDEX platform.orgs_tenant_id_idx    RENAME TO nodes_tenant_id_idx;
ALTER INDEX platform.orgs_provider_key_idx RENAME TO nodes_provider_key_idx;
ALTER INDEX platform.orgs_path_idx         RENAME TO nodes_lineage_idx;
ALTER INDEX platform.accounts_org_id_idx   RENAME TO accounts_node_id_idx;
ALTER INDEX crm.contacts_org_id_idx        RENAME TO contacts_node_id_idx;

ALTER POLICY orgs_read ON platform.nodes RENAME TO nodes_read;

COMMENT ON TABLE platform.nodes IS
  'A provider org unit -- Seccl node, SEI firm/subfirm/DSR team, ML Microsoft tenant -- conformed '
  'to one shape. `lineage` is the root->leaf ancestry INCLUDING this node, and one containment '
  'test over it filters every entity for every provider; an EMPTY lineage means global reference '
  'data that projects to every tenant. Called `orgs` until 0134, when the only enum member left '
  'in use was `node`.';
