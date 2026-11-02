"""Admin endpoints for the firm-configured catalogue (docs/platform-model.md §8).

Its own router rather than more of `admin.py`, which is already the largest module in the
tree. Everything is scoped by the tenant in the path and gated by the operations permissions
the rest of the admin surface uses: `platforms:read` to look, `platforms:write` to change.

The session is `admin_session` — `service_role`, so ops staff can act on a firm they are not a
member of, with the actor's claims published so `audit.record()` stamps who did it. The tenant
id is therefore the only scope, and it is validated against `auth.tenants` before any write:
a path parameter is caller input, and every table here is tenant-keyed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import catalogue
from app.addressing import (
    admin_tenant_id,
    catalogue_commentary_id,
    catalogue_fee_package_id,
    catalogue_model_id,
    catalogue_product_id,
    catalogue_provider_id,
    catalogue_version_id,
    platform_provider,
)
from app.auth import Principal, get_current_principal, require_permission
from app.db import admin_session
from app.permissions import PLATFORMS_READ, PLATFORMS_WRITE, Permission

router = APIRouter(prefix="/admin/tenants/{tenant_uid}/catalogue", tags=["catalogue"])


def requires(permission: Permission | str) -> Callable[..., Principal]:
    """Authorise against a fixed operations permission (`system` bypasses), the same way
    admin.py does -- a module-level singleton so ruff's B008 stays happy."""

    def _dep(principal: Principal = Depends(get_current_principal)) -> Principal:
        require_permission(principal, permission)
        return principal

    return _dep


_read = requires(PLATFORMS_READ)
_write = requires(PLATFORMS_WRITE)


# `_tenant` used to live here: it re-selected the tenant by the id the path carried, so that an
# unknown id could not write rows nobody can reach (every table here is tenant-keyed, and the FK
# would accept any integer). The path carries a UID now, and `admin_tenant_id` only yields an id for
# a tenant that exists -- so the guard has already run by the time an endpoint body starts, and
# asking again was a second query for an answer already in hand.


def _guard(error: catalogue.CatalogueError) -> HTTPException:
    """A CatalogueError is always the caller's to fix -- a mismatched fee rule, allocations
    that do not total 100 -- so it is a 422 with the message, never a 500."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error))


# ---- the whole catalogue in one call, for the admin pages ----


@router.get("", name="get-catalogue")
async def get_catalogue(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_read),
) -> dict[str, Any]:
    """Everything the catalogue pages render, in one round trip: the pages cross-reference
    each other (a fee package names a product, a provider lists products), so loading them
    separately would mean four calls to draw one screen."""
    return {
        # No session: the registry is code (0131), not a table.
        "platforms": catalogue.platforms(),
        "instruments": await catalogue.instruments(session),
        "products": await catalogue.products(session, tenant_id),
        "models": await catalogue.models(session, tenant_id),
        "fee_packages": await catalogue.fee_packages(session, tenant_id),
        # The bundled overview stays unpaged: it is the firm's whole configuration, loaded
        # once for the editor. GET /providers/{kind} below is the paginated listing.
        "third_party_providers": (await catalogue.providers(session, tenant_id, "third_party"))[
            "items"
        ],
        "transfer_providers": (
            await catalogue.providers(session, tenant_id, "transfer", limit=200)
        )["items"],
        "terms": await catalogue.terms(session, tenant_id),
    }


# ---- products ----


@router.post("/products", status_code=status.HTTP_201_CREATED, name="create-product")
async def create_product(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    payload: dict[str, Any],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_write),
) -> dict[str, Any]:
    try:
        return await catalogue.upsert_product(session, tenant_id, payload)
    except catalogue.CatalogueError as error:
        raise _guard(error) from error


@router.patch("/products/{product_uid}", name="update-product")
async def update_product(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    product_id: Annotated[int, Depends(catalogue_product_id)],
    payload: dict[str, Any],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_write),
) -> dict[str, Any]:
    try:
        return await catalogue.upsert_product(session, tenant_id, payload, product_id)
    except catalogue.CatalogueError as error:
        raise _guard(error) from error


@router.put("/products/{product_uid}/platforms/{provider}", name="set-product-platform")
async def set_product_platform(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    product_id: Annotated[int, Depends(catalogue_product_id)],
    provider: Annotated[str, Depends(platform_provider)],
    payload: dict[str, Any],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_write),
) -> dict[str, Any]:
    """What one platform calls this product, and the id it knows it by."""
    try:
        return await catalogue.set_product_platform(
            session, tenant_id, product_id, provider, payload
        )
    except catalogue.CatalogueError as error:
        raise _guard(error) from error


# ---- models ----


@router.post("/models", status_code=status.HTTP_201_CREATED, name="create-model")
async def create_model(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    payload: dict[str, Any],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_write),
) -> dict[str, Any]:
    try:
        return await catalogue.upsert_model(session, tenant_id, payload)
    except catalogue.CatalogueError as error:
        raise _guard(error) from error


@router.get("/models/{model_uid}", name="get-model")
async def get_model(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    model_id: Annotated[int, Depends(catalogue_model_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_read),
) -> dict[str, Any]:
    """The model with its whole allocation history and commentary."""
    model = await catalogue.model_detail(session, tenant_id, model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    return model


@router.patch("/models/{model_uid}", name="update-model")
async def update_model(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    model_id: Annotated[int, Depends(catalogue_model_id)],
    payload: dict[str, Any],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_write),
) -> dict[str, Any]:
    try:
        return await catalogue.upsert_model(session, tenant_id, payload, model_id)
    except catalogue.CatalogueError as error:
        raise _guard(error) from error


@router.post(
    "/models/{model_uid}/versions", status_code=status.HTTP_201_CREATED, name="create-model-version"
)
async def create_model_version(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    model_id: Annotated[int, Depends(catalogue_model_id)],
    payload: dict[str, Any],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_write),
) -> dict[str, Any]:
    """A new allocation snapshot, as a draft. Publishing it is a separate call, because an
    allocation change is reviewed before it drives a rebalance."""
    try:
        return await catalogue.create_model_version(session, tenant_id, model_id, payload)
    except catalogue.CatalogueError as error:
        raise _guard(error) from error


@router.post("/models/{model_uid}/versions/{version_uid}/publish", name="publish-model-version")
async def publish_model_version(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    model_id: Annotated[int, Depends(catalogue_model_id)],
    version_id: Annotated[int, Depends(catalogue_version_id)],
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(_write),
) -> dict[str, Any]:
    try:
        return await catalogue.publish_model_version(
            session, tenant_id, model_id, version_id, principal.user_id
        )
    except catalogue.CatalogueError as error:
        raise _guard(error) from error


@router.post(
    "/models/{model_uid}/commentary", status_code=status.HTTP_201_CREATED, name="create-commentary"
)
async def create_commentary(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    model_id: Annotated[int, Depends(catalogue_model_id)],
    payload: dict[str, Any],
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(_write),
) -> dict[str, Any]:
    try:
        return await catalogue.upsert_commentary(
            session,
            tenant_id,
            model_id,
            payload,
            author_id=principal.user_id,
        )
    except catalogue.CatalogueError as error:
        raise _guard(error) from error


@router.patch("/models/{model_uid}/commentary/{commentary_uid}", name="update-commentary")
async def update_commentary(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    model_id: Annotated[int, Depends(catalogue_model_id)],
    commentary_id: Annotated[int, Depends(catalogue_commentary_id)],
    payload: dict[str, Any],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_write),
) -> dict[str, Any]:
    try:
        return await catalogue.upsert_commentary(
            session, tenant_id, model_id, payload, commentary_id
        )
    except catalogue.CatalogueError as error:
        raise _guard(error) from error


# ---- fee packages ----


@router.post("/fee-packages", status_code=status.HTTP_201_CREATED, name="create-fee-package")
async def create_fee_package(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    payload: dict[str, Any],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_write),
) -> dict[str, Any]:
    try:
        return await catalogue.upsert_fee_package(session, tenant_id, payload)
    except catalogue.CatalogueError as error:
        raise _guard(error) from error


@router.patch("/fee-packages/{fee_uid}", name="update-fee-package")
async def update_fee_package(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    fee_id: Annotated[int, Depends(catalogue_fee_package_id)],
    payload: dict[str, Any],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_write),
) -> dict[str, Any]:
    try:
        return await catalogue.upsert_fee_package(session, tenant_id, payload, fee_id)
    except catalogue.CatalogueError as error:
        raise _guard(error) from error


# ---- instruments (global, and big) ----


@router.get("/instruments", name="list-instruments")
async def list_instruments(
    _tenant_id: Annotated[int, Depends(admin_tenant_id)],
    limit: int = 100,
    offset: int = 0,
    sort: str = "name",
    dir: str = "asc",
    q: str | None = None,
    asset_type: Annotated[list[str] | None, Query()] = None,
    currency: Annotated[list[str] | None, Query()] = None,
    exchange_code: Annotated[list[str] | None, Query()] = None,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_read),
) -> dict[str, Any]:
    """One window of the instrument universe, plus the total and the filter vocabularies.

    The Instruments table used to read the bundled catalogue's `instruments` key — the PICKER's
    first 500 by name — and search it in the browser. `platform.instruments` is global (0041: an
    ISIN is not tenant data), so it holds every tenant's securities; SEI's first sync alone landed
    6,170. A security outside those first 500 simply could not be found, and an empty table under a
    search box reads as "we do not hold that", which is a wrong answer rather than a slow one.

    The tenant is still resolved and still gates the route (`admin_tenant_id` + `platforms:read`) —
    it just does not narrow the rows, because the table is not tenant-scoped. Named `_tenant_id` so
    that is a statement rather than something a reader has to notice is missing.

    `{items, total, facets}` with limit/offset, the same contract `/providers/{kind}` and
    `/crm/accounts` use.

    `asset_type`/`currency`/`exchange_code` narrow the universe SERVER-SIDE, and have to: the
    portal only ever holds one window, so filtering there would report "no USD securities" when it
    means "none in these 200". `facets` come back beside the rows so the portal's dropdowns offer
    the values the universe actually holds rather than the ones this window happens to show.
    """
    return await catalogue.instruments_page(
        session,
        # Clamped, not trusted: an unbounded limit turns a listing into a full table scan served
        # as one JSON document.
        limit=max(1, min(limit, 500)),
        offset=max(0, offset),
        sort=sort,
        dir=dir,
        q=q,
        filters={
            "asset_type": asset_type,
            "currency": currency,
            "exchange_code": exchange_code,
        },
    )


# ---- providers (two registries, one shape) ----


@router.get("/providers/{kind}", name="list-providers")
async def list_providers(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    kind: str,
    limit: int = 25,
    offset: int = 0,
    skip: int | None = None,
    sort: str = "name",
    dir: str = "asc",
    q: str | None = None,
    product: Annotated[list[str] | None, Query()] = None,
    rail: Annotated[list[str] | None, Query()] = None,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_read),
) -> dict[str, Any]:
    """One page of a provider registry, plus the total.

    `{items, total}` with limit/offset is the contract /crm/accounts and /crm/holdings already
    use, so this does not introduce a second pagination shape into the same API. `skip` is
    accepted as an alias for `offset` because it is the more common name for it.

    Sorting has to be server-side once paging exists: the transfer register is 929 rows, and
    ordering the 25 on screen would present the page's order as the register's.
    """
    if kind not in ("third_party", "transfer"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown provider kind '{kind}'"
        )
    # Clamped, not trusted: an unbounded limit turns a listing into a full table scan served as
    # one JSON document.
    page = await catalogue.providers(
        session,
        tenant_id,
        kind,
        limit=max(1, min(limit, 200)),
        offset=skip if skip is not None else offset,
        sort=sort,
        dir=dir,
        q=q,
        product=product,
        rail=rail,
    )
    if kind == "transfer":
        # Facets ride along with the page so the toolbar's options come from the register rather
        # than from a hardcoded list, and one request serves the whole listing.
        page["facets"] = await catalogue.provider_facets(session, tenant_id)
    return page


@router.post("/providers/{kind}", status_code=status.HTTP_201_CREATED, name="create-provider")
async def create_provider(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    kind: str,
    payload: dict[str, Any],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_write),
) -> dict[str, Any]:
    """`kind` is 'third_party' or 'transfer'. Kept as one endpoint because the payload is the
    same shape; the tables differ in what a transfer provider additionally carries — and, since
    0130, in how each states its products: `transfer_providers.products` comes from the register
    and `third_party_providers.product` is one editable wrapper."""
    try:
        return await catalogue.upsert_provider(session, tenant_id, kind, payload)
    except catalogue.CatalogueError as error:
        raise _guard(error) from error


@router.patch("/providers/{kind}/{provider_uid}", name="update-provider")
async def update_provider(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    kind: str,
    provider_id: Annotated[int, Depends(catalogue_provider_id)],
    payload: dict[str, Any],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_write),
) -> dict[str, Any]:
    try:
        return await catalogue.upsert_provider(session, tenant_id, kind, payload, provider_id)
    except catalogue.CatalogueError as error:
        raise _guard(error) from error


# ---- terminology ----


@router.put("/terms/{key}", name="set-term")
async def set_term(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    key: str,
    payload: dict[str, Any],
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(_write),
) -> dict[str, Any]:
    """Set or clear one term. A blank label clears the override, so the editor's "reset"
    is the same call as an edit."""
    try:
        row = await catalogue.set_term(
            session,
            tenant_id,
            key,
            payload.get("label"),
            payload.get("plural"),
            principal.user_id,
        )
    except catalogue.CatalogueError as error:
        raise _guard(error) from error
    return {"key": key, "override": row}
