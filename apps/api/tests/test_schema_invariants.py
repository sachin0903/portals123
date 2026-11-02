"""Structural schema invariants, asserted against a real Postgres.

The per-schema FORCE loops (`0001_auth_core.sql:657`, `0002_platform_crm.sql:579`,
`0021_flows.sql:203`) are `pg_tables`-wide but ran ONCE, so a table added to auth/crm/platform/flows
in a *later* migration is not covered by them. 0026 and 0027 forced explicitly — convention, not
enforcement. docs/audit/06-core.md: "There is no test asserting 'every table in these schemas has
relforcerowsecurity'". This is that test: it is what catches the NEXT omission, in the migration
that introduces it rather than in an audit a year later.

Also pinned here, because they are schema-level invariants rather than row visibility:

  * published `documents.template_versions` really are immutable (0012 stated it in a comment only,
    and the gap was reachable — 0032 added the trigger);
  * the model tenant carries a usable role preset, so a fresh install can grant a permission at all
    (0001 seeded it blank; 0033 seeds the preset). Re-applies 0033's idempotent SQL first, because
    the DB-backed suites that truncate auth.tenants run before this file.
  * every client/account link is tenant-contained (0035): the composite (tenant_id, link) foreign
    keys, asserted by DERIVING the expected set from the catalogue rather than listing it, so the
    NEXT table someone gives a bare `contact_id` fails here instead of in an audit;
  * documents.shares' resource_type CHECK and its RLS policies stay in step (0031 asks for this in
    prose: "if the CHECK is widened again, widen this policy in the SAME migration").
"""

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

API_ROOT = Path(__file__).resolve().parent.parent  # apps/api

# Every schema whose tables are tenant-scoped and reachable from a request session.
SCHEMAS = ("auth", "crm", "platform", "flows", "documents", "tasks", "compliance")

# The only tables that legitimately have NO policy: they are deny-all for `authenticated` by
# having no grant either (service_role does the work). Listed explicitly so a future table cannot
# quietly join them by losing its policy.
DENY_ALL = frozenset(
    {
        "auth.environment_secrets",  # 0001 — only the API (service_role) reads/decrypts
        "flows.outbox",  # 0025 — the delivery queue; the deliverer runs elevated
        "flows.public_start_throttle",  # 0024 — the public-entry throttle counters
        # 0043 — the automation consumer's own machine state (its position in audit.actions, and
        # the per-firing delivery records that make redelivery idempotent). Written only by the
        # worker; a firm's "why didn't it fire?" view ships as a permission-gated endpoint
        # reading on service_role, the way audit.actions does.
        "flows.automation_cursor",
        "flows.automation_deliveries",
        # 0045 — the timer plane: a case's pending wake-ups and the triggers a parked case is
        # listening for. Engine state written only by the worker and the orchestrator; the
        # firm-facing "what is this journey waiting for?" view ships as a permission-gated
        # endpoint reading on service_role, exactly as the automation tables above.
        "flows.timers",
        "flows.case_listeners",
        # 0046 — a running journey's variables. Same posture, and for a sharper reason: memory can
        # hold anything an author promoted out of a fact-find, so the read side deserves its own
        # deliberate permission rather than inheriting the case's.
        "flows.case_memory",
        # 0048 — the estate-wide automation kill switch. It has no tenant_id at all, so
        # `auth.tenant_lineage()` has nothing to say about it and there is no claim shape a policy
        # could test; the operations endpoint gates it on `automations:global_pause` and reads it
        # on service_role. Withholding the privilege beats an always-false policy.
        "flows.automation_platform_pause",
        # 0050 — the AI meter. Written only by the worker/orchestrator on service_role, and
        # append-only even for it (no UPDATE, no DELETE grant): a meter that can be edited is not
        # a meter, and this one is the only evidence of what a firm's flows spent. Its sibling
        # `flows.ai_tenants` DOES carry a policy, because a firm is entitled to see its own switch
        # and ceiling — publish preflight reads it on the author's own session.
        "flows.ai_usage",
        # 0221 — a screening check's matches, and the firm webhook outbox. Tipping off (POCA
        # 2002 s.333A): a firm must not read an unreviewed match about its own client, and the
        # outbox is worker state. Operations reads both on service_role.
        "compliance.matches",
        "compliance.webhook_deliveries",
        # 0226 — when the worker last asked ComplyCube about a pending check. Worker state.
        "compliance.check_polls",
        # 0227 — ComplyCube's risk profile per client. Operations only: it can name a PEP.
        "compliance.client_risk",
        # 0228 — Graphene's internal notes on a client. Operations only: a note can say a client
        # is suspected.
        "compliance.client_notes",
        # 0235 — a portal client's identity photos, sealed, held only until the identity step
        # finishes. Only the API and the worker read them, to send them to ComplyCube.
        "compliance.identity_photos",
        # 0236 — the one-time link for identity photos. It is a credential (a hash of the code,
        # then of the phone's session). The API reads it on service_role; a signed-in session
        # is given the link once, in the response that made it, and never selects the row.
        "auth.identity_phone_links",
        # 0260 — a SEI money-out that may already have left. Written on the service
        # connection before the provider call. A firm session never reads the row:
        # the refusal is the action error.
        "platform.sei_money_holds",
        # 0249 — who dev and staging may email. One row for the whole environment, no tenant_id,
        # so a tenant policy has nothing to test. The admin endpoint reads it on service_role.
        "auth.outbound_email_allowlist",
        # `platform.providers` was here — 0029's `integrations.provider_sources`, moved by 0048,
        # deny-all because the source list is cross-firm operations reference data. 0133 folded it
        # into `platform.tenant_platforms`, which a firm reads its OWN row of and no other: the
        # scoping that made it deny-all is now the tenant policy, so there is no policy-less table
        # left to exempt.
    }
)

_TABLES = """
SELECT n.nspname || '.' || c.relname AS name, c.relrowsecurity, c.relforcerowsecurity
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind = 'r' AND n.nspname = ANY(:s)
"""

_POLICIES = """
SELECT schemaname || '.' || tablename AS name, count(*) AS policies,
       count(*) FILTER (WHERE 'authenticated' = ANY(roles)) AS for_authenticated
  FROM pg_policies WHERE schemaname = ANY(:s) GROUP BY 1
"""

# Tables carrying ANY privilege for the request role. aclexplode (not information_schema) so the
# answer doesn't depend on who is asking.
_AUTHENTICATED_GRANTS = """
SELECT DISTINCT n.nspname || '.' || c.relname AS name
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace,
       LATERAL aclexplode(c.relacl) a
 WHERE c.relkind = 'r' AND n.nspname = ANY(:s) AND a.grantee = 'authenticated'::regrole
"""


@pytest.fixture
def catalogue(super_engine: Engine) -> dict[str, object]:
    """The pg_catalog facts for the audited schemas, read once."""
    with super_engine.connect() as c:
        tables = {
            r.name: (r.relrowsecurity, r.relforcerowsecurity)
            for r in c.execute(text(_TABLES), {"s": list(SCHEMAS)}).mappings()
        }
        policies = {
            r["name"]: (r["policies"], r["for_authenticated"])
            for r in c.execute(text(_POLICIES), {"s": list(SCHEMAS)}).mappings()
        }
        granted = set(c.execute(text(_AUTHENTICATED_GRANTS), {"s": list(SCHEMAS)}).scalars())
    return {"tables": tables, "policies": policies, "granted": granted}


def test_the_catalogue_query_actually_found_the_schema(catalogue: dict[str, object]) -> None:
    """Guard against a vacuous pass: the audited schemas hold ~50 tables, so a typo in the
    catalogue query (which would make every assertion below trivially true) fails here."""
    tables: dict[str, object] = catalogue["tables"]  # type: ignore[assignment]
    assert len(tables) >= 40, sorted(tables)


def test_every_table_has_rls_enabled_and_forced(catalogue: dict[str, object]) -> None:
    """ENABLE + FORCE on every table in the audited schemas — FORCE so the
    table OWNER (the migrator) is bound too, which is what makes the isolation claim complete."""
    tables: dict[str, tuple[bool, bool]] = catalogue["tables"]  # type: ignore[assignment]
    missing_rls = sorted(name for name, (rls, _) in tables.items() if not rls)
    missing_force = sorted(name for name, (_, force) in tables.items() if not force)
    assert missing_rls == [], f"ENABLE ROW LEVEL SECURITY missing: {missing_rls}"
    assert missing_force == [], f"FORCE ROW LEVEL SECURITY missing: {missing_force}"


def test_every_authenticated_readable_table_has_a_policy(catalogue: dict[str, object]) -> None:
    """RLS with no policy denies everything, so a table granted to `authenticated` without one is
    a broken feature; a table granted to `authenticated` whose only policies target another role
    is the same bug wearing a disguise."""
    policies: dict[str, tuple[int, int]] = catalogue["policies"]  # type: ignore[assignment]
    granted: set[str] = catalogue["granted"]  # type: ignore[assignment]
    unprotected = sorted(name for name in granted if policies.get(name, (0, 0))[1] == 0)
    assert unprotected == [], f"granted to authenticated with no policy for it: {unprotected}"


def test_policy_less_tables_are_exactly_the_deny_all_set(catalogue: dict[str, object]) -> None:
    """The inverse check: a table with no policy at all must be one of the deliberate
    service_role-only tables, and must carry no `authenticated` grant either."""
    tables: dict[str, object] = catalogue["tables"]  # type: ignore[assignment]
    policies: dict[str, object] = catalogue["policies"]  # type: ignore[assignment]
    granted: set[str] = catalogue["granted"]  # type: ignore[assignment]
    without = {name for name in tables if name not in policies}
    assert without == set(DENY_ALL), f"unexpected policy-less tables: {sorted(without - DENY_ALL)}"
    assert without & granted == set(), "a deny-all table must have no authenticated grant"


# ---- credentials are not writable from a request session (0030) --------------------------------


CREDENTIAL_TABLES = ("auth.api_keys", "auth.environment_secrets")


@pytest.mark.parametrize("table", CREDENTIAL_TABLES)
@pytest.mark.parametrize("privilege", ("INSERT", "UPDATE", "DELETE"))
def test_the_request_role_cannot_write_credentials(
    super_engine: Engine, table: str, privilege: str
) -> None:
    """0001 granted `authenticated` INSERT/UPDATE/DELETE on auth.api_keys behind a policy whose only
    predicate was the tenant lineage — so any tenant session that reached SQL could mint itself an
    API key for its own firm, with no permission gate anywhere. Latent (no endpoint exposes it) but
    a key-minting path in the one table that holds a real credential. Keys are Graphene-issued;
    service_role keeps ALL. Asserted as a privilege invariant, not just a policy one, because a
    policy is only reached if the grant exists — and because the fix is the grant."""
    with super_engine.connect() as c:
        assert (
            c.execute(
                text("SELECT has_table_privilege('authenticated', :tbl, :priv)"),
                {"tbl": table, "priv": privilege},
            ).scalar_one()
            is False
        ), f"{privilege} on {table} is granted to authenticated"


def test_api_keys_has_no_write_policy_for_authenticated(super_engine: Engine) -> None:
    """Belt and braces: the FOR ALL policy is gone too, so nothing re-enables the write path by
    re-granting the privilege alone. Read stays — a firm listing its own keys is legitimate."""
    with super_engine.connect() as c:
        cmds = {
            r["cmd"]
            for r in c.execute(
                text(
                    "SELECT cmd FROM pg_policies WHERE schemaname = 'auth' AND tablename = "
                    "'api_keys' AND 'authenticated' = ANY(roles)"
                )
            ).mappings()
        }
    assert cmds == {"SELECT"}, f"unexpected authenticated policy commands on auth.api_keys: {cmds}"


# ---- published template versions are immutable (0032) -----------------------------------------


@pytest.fixture
def published_version(super_engine: Engine) -> dict[str, object]:
    """A firm with one template and one PUBLISHED version."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {"uid": f"ten_tpl_{s}", "alias": f"firm-tpl-{s}", "name": f"Firm Tpl {s}"},
            ).scalar_one()
        )
        author = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                ),
                {
                    "uid": f"usr_tpl_{s}",
                    "t": tenant,
                    "email": f"tpl_{s}@firm.test",
                    "wos": f"wos_tpl_{s}",
                },
            ).scalar_one()
        )
        template = int(
            c.execute(
                text(
                    "INSERT INTO documents.templates "
                    "  (uid, tenant_id, code, name, document_type, created_by) "
                    "VALUES (:uid, :t, :code, 'Suitability', 'suitability', :u) RETURNING id"
                ),
                {"uid": f"tpl_{s}", "t": tenant, "code": f"suitability_{s}", "u": author},
            ).scalar_one()
        )
        version = int(
            c.execute(
                text(
                    "INSERT INTO documents.template_versions "
                    "  (uid, tenant_id, template_id, version_number, status, renderer, schema, "
                    "   created_by, published_at) "
                    "VALUES (:uid, :t, :tpl, 1, 'published', 'weasy', "
                    "  CAST(:schema AS jsonb), :u, now()) RETURNING id"
                ),
                {
                    "uid": f"tplv_{s}",
                    "t": tenant,
                    "tpl": template,
                    "schema": '{"pages": [{"blocks": [{"type": "text"}]}]}',
                    "u": author,
                },
            ).scalar_one()
        )
    return {"tenant": tenant, "template": template, "version": version}


@pytest.mark.parametrize(
    ("column", "value"),
    [("schema", "'{}'::jsonb"), ("renderer", "'chromium'::documents.renderer")],
)
def test_published_version_content_is_immutable(
    super_engine: Engine, published_version: dict[str, object], column: str, value: str
) -> None:
    """0012_templates.sql:43 promised this in a comment; the renderer path in
    PATCH /templates/{uid} could actually mutate a live published version, breaking regeneration
    determinism for every document already pinned to it."""
    with super_engine.connect() as c, pytest.raises(DBAPIError) as excinfo:
        c.execute(
            text(
                f"UPDATE documents.template_versions SET {column} = {value} "  # noqa: S608 - fixed
                "WHERE id = :v"
            ),
            {"v": published_version["version"]},
        )
    assert "immutable" in str(excinfo.value)
    # Pinned so the API layer has something stable to map. PATCH /templates/{uid} applies a renderer
    # change to max(version_number) with NO status filter (app/routers/templates.py), so this path
    # IS reachable from the API and currently surfaces as an unhandled 500 — it should be a 409.
    # Whoever owns templates.py: catch DBAPIError, check `exc.orig.sqlstate == '23001'`, raise 409.
    assert excinfo.value.orig.sqlstate == "23001"  # type: ignore[union-attr]


def test_status_transitions_still_work_on_a_published_version(
    super_engine: Engine, published_version: dict[str, object]
) -> None:
    """The trigger must not freeze the state machine: archiving a published version, and the
    publish path's own post-flip derivations (input_form_snapshot, input_schema, fixtures), stay
    allowed."""
    with super_engine.connect() as c:
        c.execute(
            text(
                "UPDATE documents.template_versions "
                'SET input_form_snapshot = CAST(\'{"code": "f"}\' AS jsonb) WHERE id = :v'
            ),
            {"v": published_version["version"]},
        )
        c.execute(
            text("UPDATE documents.template_versions SET status = 'archived' WHERE id = :v"),
            {"v": published_version["version"]},
        )
        status = c.execute(
            text("SELECT status::text FROM documents.template_versions WHERE id = :v"),
            {"v": published_version["version"]},
        ).scalar_one()
    assert status == "archived"


def test_a_draft_version_can_still_be_edited_and_published(
    super_engine: Engine, published_version: dict[str, object]
) -> None:
    """The editor's autosave loop updates the latest DRAFT in place, then publishes it in one
    statement (documents/routers/templates.py). Neither may be blocked."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        draft = int(
            c.execute(
                text(
                    "INSERT INTO documents.template_versions "
                    "  (uid, tenant_id, template_id, version_number, status, schema, created_by) "
                    "SELECT :uid, tenant_id, template_id, 2, 'draft', '{}'::jsonb, created_by "
                    "  FROM documents.template_versions WHERE id = :v RETURNING id"
                ),
                {"uid": f"tplv_draft_{s}", "v": published_version["version"]},
            ).scalar_one()
        )
        c.execute(
            text(
                "UPDATE documents.template_versions SET schema = CAST(:s AS jsonb), "
                "  renderer = 'chromium' WHERE id = :v"
            ),
            {"s": '{"pages": []}', "v": draft},
        )
        c.execute(
            text(
                "UPDATE documents.template_versions "
                "SET status = 'published', published_at = now() WHERE id = :v"
            ),
            {"v": draft},
        )
        status = c.execute(
            text("SELECT status::text FROM documents.template_versions WHERE id = :v"),
            {"v": draft},
        ).scalar_one()
    assert status == "published"


# ---- the model tenant is bootstrappable (0033) -------------------------------------------------


@pytest.fixture
def model_preset(db: dict[str, str], super_engine: Engine) -> int:
    """Re-apply 0033's (idempotent) seed as the migrator role and return the model tenant id.

    Re-applying is deliberate: tests/test_rls.py and tests/test_rls_crm.py TRUNCATE auth.tenants
    CASCADE, which removes the migration-seeded model tenant, and they collect before this file.
    Running the seed again both restores it and proves the seed is genuinely idempotent.
    """
    sql = (API_ROOT / "migrations" / "sql" / "0033_model_role_preset.sql").read_text(
        encoding="utf-8"
    )
    engine = create_engine(db["migrator"])
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(sql)
    finally:
        engine.dispose()
    # Read back as the superuser: the migrator is FORCE-RLS-bound and no policy targets it.
    with super_engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT id FROM auth.tenants WHERE is_model ORDER BY id LIMIT 1")
            ).scalar_one()
        )


def test_model_tenant_has_a_usable_role_preset(super_engine: Engine, model_preset: int) -> None:
    """A fresh install could not grant a single permission: the model was blank, so
    provisioning.clone_model_roles cloned nothing and grant_portal_access raised LookupError —
    the firm could never be invited into. The preset must cover both portals."""
    with super_engine.connect() as c:
        roles = {
            r["slug"]: r["portal"]
            for r in c.execute(
                text("SELECT slug, portal FROM auth.roles WHERE tenant_id = :t"),
                {"t": model_preset},
            ).mappings()
        }
    assert roles == {
        "owner": "platform",
        "adviser": "platform",
        "compliance": "platform",
        "client": "client",
    }


def test_preset_grants_match_the_registry(super_engine: Engine, model_preset: int) -> None:
    """Every granted slug must exist in app/permissions.py (the single source of truth), the owner
    must be able to configure the firm, and the adviser must NOT hold contacts:read_all — that is
    what makes the book-scoping policies meaningful."""
    from app.permissions import ALL

    registry = {p.slug for p in ALL}
    with super_engine.connect() as c:
        grants: dict[str, set[str]] = {}
        for row in c.execute(
            text(
                "SELECT r.slug AS role, p.slug AS perm FROM auth.role_permissions rp "
                "JOIN auth.roles r ON r.id = rp.role_id "
                "JOIN auth.permissions p ON p.id = rp.permission_id "
                "WHERE r.tenant_id = :t"
            ),
            {"t": model_preset},
        ).mappings():
            grants.setdefault(row["role"], set()).add(row["perm"])

    assert set().union(*grants.values()) <= registry
    assert {"members:write", "roles:write", "contacts:read_all"} <= grants["owner"]
    assert "contacts:read_all" not in grants["adviser"]  # book-scoped by design
    assert "documents:write" in grants["adviser"]
    assert "contacts:read_all" in grants["compliance"]
    assert not any(p.endswith(":write") for p in grants["compliance"])  # read-only oversight
    assert "client" not in grants  # the client role holds none, by design


# ---- every client/account link is tenant-contained (0035) --------------------------------------

# The two tables a client/account link may point at, and the column that carries the tenant.
_LINK_TARGETS = ("crm.contacts", "platform.accounts")

# Link columns that deliberately have NO composite (tenant_id, link) foreign key, with the reason.
# documents.documents.account_id has never had ANY foreign key: 0005 declares it with a
# "FK -> platform.accounts added by platform_crm" comment 0002 never honoured, so it may already
# hold dangling ids and a validating constraint could fail the migration on live data. It is also
# not part of any client-visibility branch (documents scope via document_contacts, not accounts).
# Listed explicitly so it cannot quietly gain company.
UNCONSTRAINED_LINKS = frozenset({"documents.documents.account_id"})

_FOREIGN_KEYS = """
SELECT n.nspname || '.' || c.relname                    AS tbl,
       cf.relname                                       AS target,
       ARRAY(SELECT a.attname FROM pg_attribute a
              WHERE a.attrelid = k.conrelid AND a.attnum = ANY (k.conkey)
              ORDER BY a.attname)                       AS cols
  FROM pg_constraint k
  JOIN pg_class c       ON c.oid = k.conrelid
  JOIN pg_namespace n   ON n.oid = c.relnamespace
  JOIN pg_class cf      ON cf.oid = k.confrelid
  JOIN pg_namespace nf  ON nf.oid = cf.relnamespace
 WHERE k.contype = 'f'
   AND n.nspname = ANY(:s)
   AND nf.nspname || '.' || cf.relname = ANY(:t)
"""

# Columns NAMED like a client/account link on a tenant-scoped table, whether or not they carry a FK.
_LINK_SHAPED_COLUMNS = """
SELECT n.nspname || '.' || c.relname AS tbl, a.attname AS col
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
 WHERE c.relkind = 'r' AND n.nspname = ANY(:s)
   AND (a.attname LIKE '%%contact_id' OR a.attname LIKE '%%account_id')
   AND EXISTS (SELECT 1 FROM pg_attribute t
                WHERE t.attrelid = c.oid AND t.attname = 'tenant_id' AND NOT t.attisdropped)
"""


@pytest.fixture
def link_columns(super_engine: Engine) -> dict[str, set[str]]:
    """Derived from the live catalogue: for each tenant-scoped table, the client/account link
    columns with a single-column FK, and the ones carrying the composite (tenant_id, link) FK."""
    single: set[str] = set()
    composite: set[str] = set()
    with super_engine.connect() as c:
        for row in c.execute(
            text(_FOREIGN_KEYS), {"s": list(SCHEMAS), "t": list(_LINK_TARGETS)}
        ).mappings():
            cols = list(row["cols"])
            if len(cols) == 1:
                single.add(f"{row['tbl']}.{cols[0]}")
            elif len(cols) == 2 and "tenant_id" in cols:
                other = next(x for x in cols if x != "tenant_id")
                composite.add(f"{row['tbl']}.{other}")
        named = {
            f"{r['tbl']}.{r['col']}"
            for r in c.execute(text(_LINK_SHAPED_COLUMNS), {"s": list(SCHEMAS)}).mappings()
        }
    return {"single": single, "composite": composite, "named": named}


def test_every_client_link_is_tenant_contained(link_columns: dict[str, set[str]]) -> None:
    """0035's invariant, asserted structurally rather than table by table.

    A row whose contact_id/account_id points into ANOTHER firm is readable by that firm's client:
    `contact_id = ANY (auth.contact_scope())` is the client branch of a dozen read policies and
    carries no tenant conjunct. Tightening each WITH CHECK would not have closed it — none of them
    binds service_role, which is what firm_session/admin_session/the worker actually write as — so
    the containment is a composite (tenant_id, link) foreign key, which referential integrity
    enforces for every role because RI checks bypass RLS.

    Derived, not enumerated: every column with a single-column FK to crm.contacts /
    platform.accounts must ALSO have the composite. Add a table with a plain `contact_id` and this
    test names it.
    """
    missing = link_columns["single"] - link_columns["composite"] - UNCONSTRAINED_LINKS
    assert not missing, (
        "these client/account links can point outside their own row's tenant — add a composite "
        f"FOREIGN KEY (tenant_id, <col>) in a migration (see 0035): {sorted(missing)}"
    )
    # ...and the composite must not be claimed for a column that has no link FK at all.
    assert link_columns["composite"] <= link_columns["single"]


def test_the_unconstrained_link_list_has_not_grown(link_columns: dict[str, set[str]]) -> None:
    """The other half: a link-shaped column with NO foreign key at all escapes the check above
    entirely (that is exactly how documents.documents.account_id got here). Pin the set by name so
    the next one has to be justified in this file."""
    unconstrained = link_columns["named"] - link_columns["single"] - link_columns["composite"]
    assert unconstrained == UNCONSTRAINED_LINKS, (
        "a tenant-scoped table gained a client/account link column with no foreign key: "
        f"{sorted(unconstrained - UNCONSTRAINED_LINKS)}"
    )


# ---- documents.shares: the resource_type CHECK and its policies stay in step -------------------


def test_every_shares_resource_type_has_a_policy_branch(super_engine: Engine) -> None:
    """0017 created documents.shares with CHECK (resource_type IN ('document','file')) and
    0021_flows.sql:123-125 REPLACED that constraint to add 'flow_case'. So all three are rows the
    schema permits, and 0031 gives each one a branch in shares_read/shares_write.

    This is the tripwire for the prose rule 0031 states and cannot enforce. A resource_type the
    policies do not know reads as "no visible resource", i.e. the share goes invisible to the firm
    that owns it — a share nobody can find or revoke. Fail-closed is right for an UNKNOWN type; it
    is not right for a type the CHECK permits. Derived from the live constraint so widening the
    CHECK without widening the policies fails here."""
    with super_engine.connect() as c:
        check = c.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'shares_resource_type_check' "
                "  AND conrelid = 'documents.shares'::regclass"
            )
        ).scalar_one()
        policies = {
            r["policyname"]: f"{r['qual'] or ''} {r['with_check'] or ''}"
            for r in c.execute(
                text(
                    "SELECT policyname, qual, with_check FROM pg_policies "
                    "WHERE schemaname = 'documents' AND tablename = 'shares'"
                )
            ).mappings()
        }

    permitted = set(re.findall(r"'([a-z_]+)'::text", check))
    assert permitted == {"document", "file", "flow_case"}, check
    assert set(policies) == {"shares_read", "shares_write"}
    for name, body in policies.items():
        for value in permitted:
            assert f"'{value}'" in body, (
                f"documents.shares permits resource_type '{value}' but {name} has no branch for "
                "it, so such a share is invisible to the firm that owns it (and cannot be revoked)"
            )
