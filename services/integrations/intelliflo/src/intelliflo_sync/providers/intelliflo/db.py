"""Intelliflo-specific database helpers."""

import concurrent.futures
import logging

import psycopg2
import psycopg2.extras

from intelliflo_sync.providers.intelliflo import config as iflo_config
from intelliflo_sync.providers.intelliflo import views as iflo_views
from intelliflo_sync.shared import config as base_config
from intelliflo_sync.shared import db as shared_db
from intelliflo_sync.shared.lake import open_lake

logger = logging.getLogger(__name__)

S = base_config.SCHEMA_NAME

_QUERY_TIMEOUT = 300  # seconds


def _run_query(con, query: str, params: list | None = None) -> list[dict]:
    """Execute a DuckDB query with a timeout, interrupting the connection if it expires."""

    def _exec():
        result = con.execute(query, params) if params else con.execute(query)
        cols = [d[0] for d in result.description]
        return [dict(zip(cols, row, strict=True)) for row in result.fetchall()]

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_exec)
        try:
            return future.result(timeout=_QUERY_TIMEOUT)
        except concurrent.futures.TimeoutError as exc:
            con.interrupt()
            raise TimeoutError(f"DuckDB query timed out after {_QUERY_TIMEOUT}s") from exc


def _get_pg_connection():
    return shared_db._get_connection()


# ── Platform config ──────────────────────────────────────────────────────────

# platform.tenant_platforms: one row per (internal tenant, provider); `config` is jsonb. The SEI row carries the SEI
# firm id and the DSR codes the integration filters the lake by.
TENANT_PLATFORM_PROVIDER = "sei"
_FIRM_KEY = "firm_id"
# `dsr_ids` is THE key: what the admin SEI card writes, what the API validates, and the only DSR key
# the SEI sync's own slice honours (`book_sink.predicate.sei`). `dsr_codes` and `dsr_id` are this
# service's older names for the same list (a DSR "code" and a DSR team id are one value: the CODE of
# the Reference report's DSR rows, `dsr_team_id` in the lake). They are still read, as fallbacks, so
# an old config keeps narrowing instead of silently sending the whole firm to Intelliflo -- but never
# ahead of `dsr_ids`, and not quietly: the SEI slice ignores them, so a config that relies on one has
# the two syncs working from different teams.
_DSR_KEY = "dsr_ids"
_LEGACY_DSR_KEYS = ("dsr_codes", "dsr_id")
_DSR_KEYS = (_DSR_KEY, *_LEGACY_DSR_KEYS)


def _parse_dsr_value(value: object) -> list[str]:
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    return [str(c).strip() for c in items if c is not None and str(c).strip()]


def _dsr_codes(config: dict) -> list[str]:
    """DSR team ids from a tenant_platforms config: a list, or a single / comma-separated value.

    `dsr_ids` wins when it is set. A legacy name is used only when `dsr_ids` is absent or empty,
    and says so in the log (see `_DSR_KEY`)."""
    for key in _DSR_KEYS:
        value = config.get(key)
        if value in (None, "", []):
            continue
        codes = _parse_dsr_value(value)
        if key == _DSR_KEY:
            for legacy in _LEGACY_DSR_KEYS:
                stale = config.get(legacy)
                if stale not in (None, "", []) and _parse_dsr_value(stale) != codes:
                    logger.warning(
                        "tenant_platforms config carries %r=%s beside dsr_ids=%s; "
                        "using dsr_ids, the key the SEI slice reads. Remove %r.",
                        legacy,
                        _parse_dsr_value(stale),
                        codes,
                        legacy,
                    )
        else:
            logger.warning(
                "tenant_platforms config names its DSR teams under the legacy key %r; the SEI "
                "slice only reads 'dsr_ids', so this sync filters by teams that slice ignores. "
                "Move them to 'dsr_ids'.",
                key,
            )
        return codes
    return []


def _firm_and_dsrs(config: dict | None) -> tuple[str, list[str]]:
    """(SEI firm id, DSR codes) from a tenant_platforms config; ("", []) when it has none."""
    config = config or {}
    return str(config.get(_FIRM_KEY) or "").strip(), _dsr_codes(config)


class LakeSession:
    """One lake connection (with the Intelliflo views on it) that a job shares between its reads.

    Opened on first use and closed when the job ends, so a firm's job attaches the lake once instead of
    once per feature, and a feature that never reads the lake (IncomeStatements) never opens it. It is
    created BY the job and passed down explicitly: nothing is global, two jobs never share a connection,
    and a read function called without one (a single-feature `run-once`, a test) opens and closes its
    own, exactly as before.
    """

    def __init__(self) -> None:
        self._con = None

    def connection(self):
        if self._con is None:
            con = open_lake()
            iflo_views.create_views(con)
            self._con = con
        return self._con

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    def __enter__(self) -> "LakeSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _lake_connection(lake: "LakeSession | None"):
    """(connection, release) for a read: the caller's shared session, left open for the job to close, or a
    private connection that `release` closes."""
    if lake is not None:
        return lake.connection(), lambda: None
    con = open_lake()
    iflo_views.create_views(con)
    return con, con.close


def get_platform_config(tenant_id: str, provider: str = TENANT_PLATFORM_PROVIDER) -> dict | None:
    """Return the platform row for a Intelliflo tenant, or None if not installed.

    `firm_id` is the SEI firm id and `dsr_codes` the DSR codes, both read from platform.tenant_platforms by the
    platform's own firm_id (the internal tenant id); `internal_firm_id` is that platform firm_id. `provider` picks
    which tenant_platforms row (provider_code) that comes from — 'sei' for now.
    """
    conn = _get_pg_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # to_jsonb(p) so a column the schema does not have yet reads as missing instead of failing the
            # query — the feature that needs it is blocked by the contract check, the others keep running.
            cur.execute(
                f"SELECT to_jsonb(p) AS platform FROM {S}.platforms p WHERE p.tenant_id = %s",
                (tenant_id,),
            )
            found = cur.fetchone()
            if not found:
                return None
            row = found["platform"]
            internal = str(row.get("firm_id") or "").strip()
            config = None
            if internal.isdigit():
                cur.execute(
                    f"SELECT config FROM {iflo_config.PLATFORM_SCHEMA}.tenant_platforms "
                    "WHERE tenant_id = %s AND provider = %s",
                    (int(internal), provider),
                )
                tenant_platform = cur.fetchone()
                config = tenant_platform["config"] if tenant_platform else None
    finally:
        conn.close()
    firm_id, dsr_codes = _firm_and_dsrs(config)

    def text(key: str) -> str:
        return str(row.get(key) or "").strip()

    return {
        "platform_id": str(row["id"]),
        "tenant_id": row["tenant_id"],
        "internal_firm_id": internal,
        "provider": provider,
        "firm_id": firm_id,
        "dsr_codes": dsr_codes,
        "provider_id": text("provider_id"),
        "provider_name": text("provider_name"),
        "app_id": text("app_id"),
        "group_id": text("group_id"),
    }


def set_platform_group_id(platform_id: str, group_id: str) -> None:
    """Store the Intelliflo group id on the platform row so later runs skip the group lookup."""
    conn = _get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {S}.platforms SET group_id = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (group_id, platform_id),
            )
        conn.commit()
    finally:
        conn.close()


# The custodian whose account numbers the lake carries: the shared datalake is SEI's.
_CUSTODY_PROVIDER = "sei"


def _firm_scope(internal_firm_id: object) -> int | None:
    """The portals tenant an integration belongs to (`platform_config["internal_firm_id"]`), or None
    when it has none.

    Every account lookup below is scoped to it, and a missing one means "no accounts", never "all
    of them": SEI account numbers are NOT unique across portals tenants. Several tenants hold links
    to the same SEI firm's accounts (graphene and chester-rose both carry firm 3106's), each with
    its own Intelliflo links, so a lookup by number alone can resolve another tenant's Intelliflo
    plan and post this tenant's valuations or transactions into it."""
    value = str(internal_firm_id or "").strip()
    return int(value) if value.isdigit() else None


def _custody_link_join(schema: str) -> str:
    """The join that gives an account its lake account number: ``cl.external_id``.

    The number lives on the custodian's primary ``platform.links`` row for the account and nowhere
    else: no column on ``platform.accounts`` is consulted, anywhere in this service. The book-sink
    merge writes the number to that link and to nothing on the account row, so a second source for
    it would be a second source of truth, and drift.
    """
    return f"""
                JOIN {schema}.links cl
                  ON cl.tenant_id = a.tenant_id
                 AND cl.entity_type = 'account'
                 AND cl.entity_id = a.id
                 AND cl.link_type = 'primary'
                 AND cl.provider = '{_CUSTODY_PROVIDER}'"""


def _fetch_account_links(internal_firm_id: object, account_numbers: list[str]) -> list[tuple[str, str, str | None]]:
    """(account number, policy number, plan id) for each Intelliflo link of the given accounts,
    within the one portals tenant (`_firm_scope`).

    An account is found by the external_id of its custodian link (`_custody_link_join`); its
    Intelliflo link (entity_type 'account', provider 'intelliflo', link_type 'primary') carries the
    policy number in external_id and the plan id in data->>'id'.
    """
    tenant = _firm_scope(internal_firm_id)
    if tenant is None or not account_numbers:
        return []
    P = iflo_config.PLATFORM_SCHEMA
    conn = _get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT cl.external_id, l.external_id, l.data ->> 'id'
                FROM {P}.links l
                JOIN {P}.accounts a ON a.id = l.entity_id AND a.tenant_id = l.tenant_id{_custody_link_join(P)}
                WHERE l.tenant_id = %s
                  AND l.entity_type = 'account'
                  AND l.provider = 'intelliflo'
                  AND l.link_type = 'primary'
                  AND cl.external_id = ANY(%s)
                """,
                (tenant, list(account_numbers)),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_policy_numbers_for_accounts(internal_firm_id: object, account_numbers: list[str]) -> dict[str, list[str]]:
    """Map SEI account number → Intelliflo policy number(s) from the platform links table, for one
    portals tenant.

    Accounts with no link are absent from the result; an account that resolves to more than one
    distinct policy number has several entries.
    """
    result: dict[str, list[str]] = {}
    for account_number, external_id, _plan_id in _fetch_account_links(internal_firm_id, account_numbers):
        external_id = (external_id or "").strip()
        if external_id and external_id not in result.get(account_number, []):
            result.setdefault(account_number, []).append(external_id)
    return result


def get_plan_links_for_accounts(
    internal_firm_id: object, account_numbers: list[str]
) -> dict[str, list[tuple[str, str]]]:
    """Map SEI account number → [(plan id, policy number)] from the platform links table, for one
    portals tenant.

    Links without a policy number or plan id are ignored. An account with more than one distinct
    (plan id, policy number) pair has several entries.
    """
    result: dict[str, list[tuple[str, str]]] = {}
    for account_number, external_id, plan_id in _fetch_account_links(internal_firm_id, account_numbers):
        external_id, plan_id = (external_id or "").strip(), (plan_id or "").strip()
        if external_id and plan_id and (plan_id, external_id) not in result.get(account_number, []):
            result.setdefault(account_number, []).append((plan_id, external_id))
    return result


def get_transaction_targets(
    internal_firm_id: object, account_numbers: list[str]
) -> dict[str, list[tuple[str, str, str]]]:
    """Map SEI account number → [(Intelliflo client id, plan id, policy number)] from the platform
    links table, for one portals tenant.

    The plan comes from the account's Intelliflo link, as for valuations. The client comes from the link of the
    account's contact (platform.accounts.contact_id → links entity_type 'contact', provider 'intelliflo',
    link_type 'primary'), whose external_id is the Intelliflo client id. Accounts missing either are absent; an
    account that resolves to more than one distinct target has several entries.
    """
    tenant = _firm_scope(internal_firm_id)
    if tenant is None or not account_numbers:
        return {}
    P = iflo_config.PLATFORM_SCHEMA
    conn = _get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT cl.external_id, c.external_id, l.data ->> 'id', l.external_id
                FROM {P}.links l
                JOIN {P}.accounts a ON a.id = l.entity_id AND a.tenant_id = l.tenant_id{_custody_link_join(P)}
                JOIN {P}.links c
                  ON c.entity_type = 'contact'
                 AND c.entity_id = a.contact_id
                 AND c.tenant_id = l.tenant_id
                 AND c.provider = 'intelliflo'
                 AND c.link_type = 'primary'
                WHERE l.tenant_id = %s
                  AND l.entity_type = 'account'
                  AND l.provider = 'intelliflo'
                  AND l.link_type = 'primary'
                  AND cl.external_id = ANY(%s)
                """,
                (tenant, list(account_numbers)),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    result: dict[str, list[tuple[str, str, str]]] = {}
    for account_number, client_id, plan_id, policy_number in rows:
        target = ((client_id or "").strip(), (plan_id or "").strip(), (policy_number or "").strip())
        if all(target) and target not in result.get(account_number, []):
            result.setdefault(account_number, []).append(target)
    return result


def upsert_tenant(
    tenant_id: str,
    tenant_name: str,
    app_id: str,
    group_id: str | None,
    application_data: dict,
    provider_id: str | None = None,
    provider_name: str | None = None,
) -> str:
    """Create or refresh the Intelliflo platform row for a tenant from the tenants API; returns its id.

    application_data is replaced with the tenant response. An existing row keeps its status, enabled, installed and
    firm; a new one is created pending, disabled, with no firm, for an admin to approve. A row is matched on
    (platform_name, tenant_id) — as the webhook does — because the firm is not known here.

    `provider_id` / `provider_name` are the defaults to start from: written when the row is CREATED, and on an
    existing row only where the column is blank or NULL. A value already there (an admin's change per tenant) is
    never overwritten.
    """
    conn = _get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM {S}.platforms WHERE platform_name = 'intelliflo' AND tenant_id = %s "
                "ORDER BY id LIMIT 1",
                (tenant_id,),
            )
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    f"""
                    UPDATE {S}.platforms SET
                        tenant_name = %s,
                        app_id = %s,
                        group_id = COALESCE(%s, group_id),
                        application_data = %s,
                        provider_id = COALESCE(NULLIF(provider_id, ''), %s),
                        provider_name = COALESCE(NULLIF(provider_name, ''), %s),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING id
                    """,
                    (
                        tenant_name,
                        app_id,
                        group_id,
                        shared_db._SafeJson(application_data),
                        provider_id or None,
                        provider_name or None,
                        existing[0],
                    ),
                )
            else:
                cur.execute(
                    f"""
                    INSERT INTO {S}.platforms
                        (platform_name, tenant_id, tenant_name, status, enabled, installed,
                         app_id, group_id, application_data, provider_id, provider_name)
                    VALUES ('intelliflo', %s, %s, 'pending', false, true, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        tenant_id,
                        tenant_name,
                        app_id,
                        group_id,
                        shared_db._SafeJson(application_data),
                        provider_id or None,
                        provider_name or None,
                    ),
                )
            row = cur.fetchone()
        conn.commit()
        return str(row[0])
    finally:
        conn.close()
