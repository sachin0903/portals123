"""Platform catalog reads — products, models, fee packages.

Reference data for account setup (the onboarding ``setup_account`` step and any account-creation
UI). Products, models and fee packages are firm-confidential tenant reference data; the products
endpoint returns only the tenant's enabled shelf and is also the Accounts listing's authoritative
Type-filter vocabulary. `products` stays permission-gate-free because client-facing onboarding
reads it, but tenant RLS still scopes it. `models`/`fee-packages` carry a **defence-in-depth
staff-permission gate** *on top of* RLS: RLS already
returns nothing for the literal ``'client'`` role (empty ``tenant_lineage()``), but that lever keys
on the role-slug *string* — a firm whose client role is slugged something other than ``'client'``
would otherwise see them. Requiring any staff read capability (clients hold none) closes that gap
regardless of the slug. These are the first HTTP surface over the ``platform`` catalogue.

Consumers (keep this list honest -- an endpoint nobody calls is a liability, not a feature):

* ``products`` -- onboarding ``setup_account`` and the Accounts Type filter.
* ``custodians`` -- the journey kickoff's custodian picker (the firm's ENABLED custodians,
  each with ``openable_custodies`` — Seccl is onshore only; SEI follows ``custody_orgs``).
* ``models`` -- the Accounts model filter and the platform client record's model top-up /
  model withdrawal dialogs (``apps/platform/src/routes/clients/[id]``), which post ``model_id``.
* ``fee-packages`` -- the platform journey kickoff (``/journeys/new``) optional fee
  package picker, and any account-setup surface that assigns ``fee_package_id``.
* ``transfer-providers`` -- the platform portal's transfer composer and the journey
  kickoff transfer-in picker.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import catalogue, providers
from app.auth import Principal, get_current_principal, require_any_permission
from app.db import book_session
from app.db import target_tenant_session as tenant_session
from app.listing import DEFAULT_LIMIT
from app.permissions import CASES_READ, CONTACTS_READ, FLOWS_READ

router = APIRouter(prefix="/catalog", tags=["catalog"])


def _known_filter(provider: str | None) -> str | None:
    """A provider FILTER, refused rather than cast when it names nothing.

    `platform.provider_code` has three members, so an unrecognised value reaching
    `CAST(:pid AS platform.provider_code)` raises `invalid input value for enum` — a 500 that also
    aborts the transaction for anything after it in the request. And the value an old bookmark, a
    cached page or a pre-0138 admin client sends is `morgan_lloyd`, the spelling this estate used
    until that migration. 422 rather than 404: the resource exists, the filter does not name a
    provider. `addressing._known_provider` does the same for a path segment.
    """
    if provider is None or provider == "":
        return None
    if provider not in providers.PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown provider '{provider}'; expected one of "
            + ", ".join(sorted(providers.PROVIDERS)),
        )
    return provider


@router.get("/products", name="list-products")
async def list_products(
    provider: str | None = None,
    session: AsyncSession = Depends(tenant_session),
) -> list[dict[str, object]]:
    """The tenant's enabled product shelf, optionally narrowed to one platform.

    THE PLATFORM COMES FROM `platform.product_platforms`, NOT from `platform.products.platform_id`
    — and reading the wrong one had emptied this endpoint outright. `products.platform_id` is the
    nullable legacy column 0002 created and 0044 superseded (`0041_platform_conformed` states it
    in as many words: "the platform_identifier columns 0002 put on accounts/products/models are
    superseded by" the join tables, and `0063` went on to DROP the models one for exactly that
    reason). Nothing has written it since: the pipeline's `_PRODUCTS` insert names
    `tenant_id, product_type, name, code, platform_identifier, enabled` and no `platform_id`, so
    every row on a real book holds NULL — and an INNER JOIN on NULL returns nothing. The three
    surfaces that read this (the accounts filter, the new-journey product picker and the flow
    builder's `setup_account` step) were each looking at an empty shelf on a firm with seven
    products, which reads as "this firm offers nothing" rather than as a broken join.

    ONE ROW PER PRODUCT still, with the platform picked from the mapping rather than fanned out.
    A shelf item is ONE thing however many custodians hold it — that is the whole reason 0044
    moved the reference to the child and 0122 gave the child a `role` — so the custodian's
    mapping is what a caller asking "where is this product held" means, and a product mapped to a
    scheme administrator as well must not appear twice on a picker. `role = 'custodian'` is
    therefore preferred and any mapping is the fallback (`role` is nullable: 0122 backfilled it
    from the provider's own role and a hand-made row may still leave it unstated).

    A product with NO mapping keeps its row with a null platform, because a firm's shelf item is a
    shelf item before a custodian is wired to it — which is the state TPONBOND and TPOFFBOND are
    in on every book (`0128` seeded them and no provider publishes them). Dropping it would put
    this endpoint straight back to answering "nothing" for a product the firm demonstrably offers.
    """
    # Deliberately permission-ungated because client-facing onboarding reads it. Tenant RLS still
    # scopes the shelf; permission-audit-reviewed, not an oversight.
    rows = (
        (
            await session.execute(
                text(
                    "SELECT pr.id, pr.uid, pr.product_type, COALESCE(pr.code, "
                    "         pr.product_type::text) AS code, pr.name, pr.description, "
                    "       pr.is_pension, pr.enabled, "
                    "       pr.allowed_custodies::text[] AS allowed_custodies, "
                    "       pr.allowed_client_types::text[] AS allowed_client_types, "
                    "       pr.max_parties, pr.min_age, pr.max_age, pr.bank_details_allowed, "
                    "       m.provider, m.role, "
                    "       ARRAY(SELECT pp.provider::text "
                    "         FROM platform.product_platforms pp "
                    "         JOIN platform.tenant_platforms tp "
                    "           ON tp.tenant_id = pr.tenant_id "
                    "          AND tp.provider = pp.provider AND tp.enabled "
                    "        WHERE pp.product_id = pr.id AND pp.enabled "
                    "          AND pp.role = 'custodian' "
                    "        ORDER BY pp.provider)::text[] AS custodians "
                    "FROM platform.products pr "
                    "LEFT JOIN LATERAL ("
                    "  SELECT pp.provider, pp.role "
                    "    FROM platform.product_platforms pp "
                    "   WHERE pp.product_id = pr.id AND pp.enabled "
                    "   ORDER BY (pp.role = 'custodian') DESC NULLS LAST, pp.provider "
                    "   LIMIT 1"
                    ") m ON true "
                    "WHERE (CAST(:pid AS text) IS NULL OR EXISTS ("
                    "       SELECT 1 FROM platform.product_platforms wanted "
                    "        WHERE wanted.product_id = pr.id AND wanted.enabled "
                    "          AND wanted.provider = CAST(:pid AS platform.provider_code))) "
                    "  AND pr.enabled IS NOT FALSE "
                    "ORDER BY pr.sort_order, pr.name"
                ),
                {"pid": _known_filter(provider)},
            )
        )
        .mappings()
        .all()
    )
    # The bounds the kickoff wizard enforces are the EFFECTIVE ones — the row's, narrowed by any
    # statutory ceiling (`catalogue.effective_age_bounds`). Resolved here rather than in the wizard
    # so the rule is written once, on the side that owns it: the wizard shows the error and
    # disables Continue purely from `min_age`/`max_age`, so a JISA whose row carries neither (see
    # STATUTORY_MAX_AGE for why that is the norm, not an edge case) let an adult straight through.
    out: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        item["min_age"], item["max_age"] = catalogue.effective_age_bounds(
            str(item.get("product_type") or ""), item.get("min_age"), item.get("max_age")
        )
        item["allowed_client_types"] = catalogue.effective_client_types(
            str(item.get("product_type") or ""), item.get("allowed_client_types")
        )
        out.append(item)
    return out


@router.get("/custodians", name="list-custodians")
async def list_custodians(
    session: AsyncSession = Depends(tenant_session),
    principal: Principal = Depends(get_current_principal),
) -> list[dict[str, object]]:
    """The custodians THIS FIRM can open an account with, staff-only (its own enablements — a
    sub-tenant's are not the parent's, and RLS alone would hand over both).

    Scoped to the firm's own enabled platforms (``platform.tenant_platforms``): a journey started
    against a custodian the firm is not enabled on produces an account no pipeline will ever reach,
    so offering it is offering a choice that cannot be honoured. Narrower still than that table,
    which records every counterparty we pipeline: Morgan Lloyd is a **scheme administrator**, and
    an account cannot be addressed to something that cannot hold it. A row whose provider the
    registry does not know is included rather than withheld — an unregistered platform is more
    likely ours to name than one to hide.

    Narrower than the whole provider registry, which is every counterparty we pipeline: Morgan
    Lloyd is a **scheme administrator**, and offering it here would let an account be addressed to
    something that cannot hold it.

    NAMED FROM CODE since 0131 — `platform.platforms` is gone and its `id` was a surrogate for the
    provider code, so `catalogue.platforms()` supplies the id and the name. SCOPED FROM THE TABLE,
    which is the half the registry cannot answer: `tenant_platforms` says which custodians THIS
    firm is enabled on, and it is read with the tenant stated rather than left to RLS.
    `tenant_platforms_read` scopes by `auth.tenant_lineage()` — the caller's tenant PLUS its
    descendants — so leaving it to the policy offered a parent firm a custodian only a sub-tenant
    is enabled on (the account-no-pipeline-reaches case), once per enabling tenant.
    """
    require_any_permission(principal, CONTACTS_READ, CASES_READ, FLOWS_READ)
    enabled = (
        (
            await session.execute(
                text(
                    "SELECT tp.provider::text AS provider, tp.config "
                    "FROM platform.tenant_platforms tp "
                    "WHERE tp.tenant_id = auth.tenant_id() AND tp.enabled"
                )
            )
        )
        .mappings()
        .all()
    )
    configs = {str(row["provider"]): dict(row["config"] or {}) for row in enabled}
    return [
        {
            **row,
            "openable_custodies": sorted(
                providers.openable_custodies(
                    str(row["provider"]), configs.get(str(row["provider"]))
                )
            ),
        }
        for row in catalogue.platforms()
        if str(row["provider"]) in configs and _is_custodian(str(row["provider"]))
    ]


def _is_custodian(provider: str) -> bool:
    meta = providers.PROVIDER_META.get(provider)
    return meta is None or meta.get("role") == "custodian"


@router.get("/models", name="list-models")
async def list_models(
    provider: str | None = None,
    session: AsyncSession = Depends(book_session),
    principal: Principal = Depends(get_current_principal),
) -> list[dict[str, object]]:
    """The firm's investment models (RLS-scoped to the tenant), optionally one platform's.
    Staff-only (defence-in-depth over RLS — see module docstring).

    Book-enabled as a READ (``?tenant=<uid>`` + ``book:read``, ``db.book_session``): the admin
    proxy's model-mandate and model top-up/withdrawal forms need the target firm's model list.
    The minted claims carry the TARGET's lineage, so RLS returns that firm's models and only
    those; the staff gate below is satisfied by the book overlay's ``contacts:read``."""
    require_any_permission(principal, CONTACTS_READ, CASES_READ, FLOWS_READ)
    rows = (
        (
            await session.execute(
                text(
                    "SELECT m.id, m.provider, m.tenant_id, m.name, m.products, m.enabled "
                    "FROM platform.models m "
                    "WHERE (CAST(:pid AS text) IS NULL "
                    "       OR m.provider = CAST(:pid AS platform.provider_code)) "
                    "  AND m.enabled IS NOT FALSE "
                    "ORDER BY m.name"
                ),
                {"pid": _known_filter(provider)},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


@router.get("/transfer-providers", name="list-transfer-providers")
async def list_transfer_providers(
    q: str | None = None,
    limit: int = DEFAULT_LIMIT,
    session: AsyncSession = Depends(tenant_session),
    principal: Principal = Depends(get_current_principal),
) -> list[dict[str, object]]:
    """Ceding schemes a transfer can be addressed to, for the composer's provider picker.

    Search-as-you-type rather than a full list: the register runs to several hundred rows per
    firm, so shipping all of them to populate a dropdown would be the page's largest payload.
    ``limit`` is clamped for the same reason.

    Only ``enabled`` providers, because a firm switching one off is saying it will not transfer
    from them — and only those with a ``masterparty_id``, since that is the id the provider call
    is addressed to (Seccl's ``previousProvider``) and a row without one cannot be submitted.
    """
    require_any_permission(principal, CONTACTS_READ, CASES_READ, FLOWS_READ)
    term = (q or "").strip()
    rows = (
        (
            await session.execute(
                text(
                    "SELECT p.id, p.name, p.masterparty_id, p.origo_id, p.atg_reference, "
                    "       p.status, p.rails "
                    "FROM platform.transfer_providers p "
                    "WHERE p.enabled IS NOT FALSE "
                    "  AND p.masterparty_id IS NOT NULL "
                    "  AND (:term = '' OR p.name ILIKE '%' || :term || '%') "
                    "ORDER BY p.name "
                    "LIMIT :limit"
                ),
                {"term": term, "limit": max(1, min(limit, 100))},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


@router.get("/fee-packages", name="list-fee-packages")
async def list_fee_packages(
    product_id: int | None = None,
    model_id: int | None = None,
    session: AsyncSession = Depends(tenant_session),
    principal: Principal = Depends(get_current_principal),
) -> list[dict[str, object]]:
    """The firm's fee packages (RLS-scoped), optionally filtered by product or model.
    Staff-only (defence-in-depth over RLS — see module docstring)."""
    require_any_permission(principal, CONTACTS_READ, CASES_READ, FLOWS_READ)
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, tenant_id, provider, product_id, model_id, name, category, "
                    "       frequency, fee_type, rule, access, description, enabled, is_default, "
                    "       optional, sort_order, currency, vatable, "
                    # What the percentage is taken OF, who pays it and the limits around it: a
                    # picker that names a package without its charge asks an adviser to agree to
                    # pricing they cannot see. The reader formats them (`fee-summary.ts`).
                    "       charge_basis, payer, minimum_amount, maximum_amount "
                    "FROM platform.fee_packages "
                    "WHERE (CAST(:prod AS int) IS NULL OR product_id IS NULL "
                    "       OR product_id = CAST(:prod AS int)) "
                    "  AND (CAST(:model AS int) IS NULL OR model_id IS NULL "
                    "       OR model_id = CAST(:model AS int)) "
                    "  AND enabled IS NOT FALSE "
                    "ORDER BY sort_order, name"
                ),
                {"prod": product_id, "model": model_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]
