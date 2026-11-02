"""Give the local dev tenant the branding an email needs: portal hosts, storage, and a LOGO.

`bootstrap_dev_tenant.py` creates the tenant, the user and the roles — everything login needs — and
deliberately no branding. That leaves every email on the text wordmark, which is the correct
fallback and looks exactly like a bug: `shell.masthead` renders the firm's name set in type
whenever `brand.logo_url` is None, and it is None until three separate things exist.

This script creates all three, idempotently:

1. **A portal host** (`auth.tenant_environments`). An email's logo is an absolute URL, and the host
   is where it comes from (`email/transport.py::asset_origin`). With no host row there is no origin
   to state, so a stored logo cannot be turned into something a mail client can fetch.
2. **Object storage** (`storage.tenants`). Without a bucket, `put_branding_asset` falls to a base64
   `data:` URI — which the portals render happily and every mail client blocks, so the masthead
   refuses it and falls back to type. A logo that only works on screen is the subtlest version of
   this bug, and the one a preview cannot show you.
3. **A logo**, from `DEV_BRAND_LOGO` if you point it at one, else the portals' own app icon.

   THE APP ICON IS NOT THE BRAND LOCKUP. It is the bright cyan mark meant for a home screen, and
   against the portals' light chrome it reads as the wrong artwork — which is exactly what happened
   when this script installed it on the OPERATIONS tenant and the admin sidebar started wearing it.
   Graphene's own navy-and-teal lockup is not a file in this repo. Point `DEV_BRAND_LOGO` at the
   real file, or upload it at `/branding`, which is the path a firm uses.

   A PNG or JPEG either way, and that is a constraint rather than a preference: Gmail and Outlook
   refuse SVG outright, so an SVG logo is attached to the mail and arrives blank.

Run:  PYTHONPATH=. uv run --directory apps/api python scripts/bootstrap_dev_branding.py
Env:  DEV_TENANT_ALIAS (default `graphene`), DEV_CLIENT_HOST, DEV_PLATFORM_HOST,
      DEV_BRAND_LOGO (a PNG/JPEG path), DEV_BRAND_FORCE=1 to replace branding a tenant has.
"""

import asyncio
import os
import pathlib
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import branding as branding_mod
from app import storage, tenant_storage
from app.config import settings
from app.email import transport as email_transport

TENANT_ALIAS = os.environ.get("DEV_TENANT_ALIAS", "graphene")
# The dev ports this checkout serves on. They are what the portals are reached at, so they are what
# an email's asset URL has to name. An existing host is never overwritten (see the COALESCE below).
CLIENT_HOST = os.environ.get("DEV_CLIENT_HOST", "portals2.localhost:5183")
PLATFORM_HOST = os.environ.get("DEV_PLATFORM_HOST", "portals2.localhost:5185")

#: The portals' own app icon — the LAST resort, and NOT the brand. Relative to this file so it
#: does not depend on the cwd; every portal ships the same set.
FALLBACK_PNG = (
    pathlib.Path(__file__).resolve().parents[3] / "apps/platform/static/apple-touch-icon.png"
)

#: A real logo to install instead. Set it and the tenant gets the firm's actual artwork.
BRAND_LOGO = os.environ.get("DEV_BRAND_LOGO", "")

#: Replace branding a tenant ALREADY has. Off by default: this script exists to furnish an
#: EMPTY dev tenant, and it ran against the operations tenant and overwrote Graphene's own logo
#: set with an app icon — the admin portal wears that set, so the whole internal surface changed
#: brand. A seed must not be able to do that by accident.
FORCE = os.environ.get("DEV_BRAND_FORCE", "") not in ("", "0", "false", "False")

#: Both portals, typed as the branding module types them — so the two loops below need no cast.
PORTALS: tuple[branding_mod.Portal, ...] = ("client", "platform")


def _logo_bytes() -> tuple[bytes, str, str]:
    """`(bytes, content_type, what it is)` for the image to install.

    `DEV_BRAND_LOGO` wins; otherwise the portals' app icon, LABELLED as a stand-in rather than
    presented as the brand — it is the wrong artwork for light chrome, and saying so in the output
    is what stops it being mistaken for the real thing twice.
    """
    chosen = pathlib.Path(BRAND_LOGO) if BRAND_LOGO else FALLBACK_PNG
    if not chosen.is_file():
        raise SystemExit(f"No image at {chosen}. Point DEV_BRAND_LOGO at a PNG or JPEG.")
    suffix = chosen.suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg"):
        # Not a taste call: Gmail and Outlook refuse SVG, so an SVG logo is attached to the mail
        # and arrives blank. Refusing here beats debugging an empty masthead later.
        raise SystemExit(f"{chosen.name} is not a PNG or JPEG — a mail client will not draw it.")
    data = chosen.read_bytes()
    if len(data) > storage.MAX_ASSET_BYTES:
        raise SystemExit(
            f"{chosen.name} is {len(data)} bytes, over the {storage.MAX_ASSET_BYTES}-byte asset "
            "cap. Re-export it smaller."
        )
    kind = "image/png" if suffix == ".png" else "image/jpeg"
    label = str(chosen) if BRAND_LOGO else f"{chosen.name} — the APP ICON, not the brand lockup"
    return data, kind, label


async def main() -> None:
    engine = create_async_engine(settings.migration_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as s, s.begin():
        # `service_role` in EVERY block below, not just this one: each table here is RLS-protected
        # and a plain session carries no claims, so `auth.tenants` is simply invisible to it — which
        # surfaces as provisioning failing to find the tenant's own uid rather than as a permission
        # error.
        await s.execute(text("SET LOCAL ROLE service_role"))
        row = (
            (
                await s.execute(
                    text("SELECT id, uid, name FROM auth.tenants WHERE alias = :a"),
                    {"a": TENANT_ALIAS},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            print(
                f"No tenant with alias '{TENANT_ALIAS}'. Run scripts/bootstrap_dev_tenant.py first.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        tenant_id, tenant_uid = int(row["id"]), str(row["uid"])
        brand_name = str(row["name"] or "Graphene")

        # 1. THE HOSTS. `stage` is part of the KEY — `tenant_environments_tenant_stage_key` is
        # `(tenant_id, stage)`, not `tenant_id` alone — and a deployment only ever writes its own.
        # COALESCE so a re-run cannot overwrite hosts somebody has already set; each column also
        # carries its own unique index (0099), so two tenants can never claim one host.
        await s.execute(
            text(
                "INSERT INTO auth.tenant_environments "
                "  (tenant_id, stage, client_portal_host, platform_host) "
                "VALUES (:t, :stage, :client, :platform) "
                "ON CONFLICT (tenant_id, stage) DO UPDATE SET "
                "  client_portal_host = COALESCE(auth.tenant_environments.client_portal_host, "
                "                                EXCLUDED.client_portal_host), "
                "  platform_host = COALESCE(auth.tenant_environments.platform_host, "
                "                           EXCLUDED.platform_host)"
            ),
            {
                "t": tenant_id,
                "stage": settings.app_stage,
                "client": CLIENT_HOST,
                "platform": PLATFORM_HOST,
            },
        )

    # 2. STORAGE. Its own transaction: it talks to the object store, and a failure there must not
    # roll back the hosts above. Skipped rather than failed with no platform credential — the
    # upload then lands as a `data:` URI, and the summary below says so.
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        if tenant_storage.provisioning_configured():
            summary = await tenant_storage.provision_tenant_storage(s, tenant_id)
            print(f"storage: {summary.get('steps', summary)}")
        else:
            print("storage: SKIPPED (no S3 credential) — the logo will be a data: URI")

    # 3. THE LOGO, through the same door the branding page uses, so it lands where the relay reads
    # it and comes back as the `/_assets/{name}` path everything else in the product stores.
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        # ALREADY BRANDED? Leave it alone. The hosts above are COALESCEd for exactly this reason,
        # and the logo NOT being was the asymmetry that let this overwrite Graphene's own mark.
        #
        # `stored_branding`, NOT `get_branding`: the latter answers the EDITOR, so its logo sits
        # nested under a `branding` key beside the firm name and the client roles. Read as though
        # it were flat it reports None for a firm that has a logo — which is how this guard passed
        # on a branded tenant, and how the write below came to replace the whole branding object.
        existing = await branding_mod.stored_branding(s, tenant_id, PORTALS[0])
        if not FORCE and (existing.logo_url or existing.icon_url):
            print(
                f"branding: KEPT — this tenant already has one ({existing.logo_url}). "
                "DEV_BRAND_FORCE=1 to replace it."
            )
            await engine.dispose()
            return

        data, content_type, label = _logo_bytes()
        print(f"logo: {label}")
        # The same artwork under both kinds: the masthead wears one, the Brand icon block places
        # the other. The keys differ (`logo-` / `icon-`) so either can be replaced on its own, and
        # content hashing means the bytes are stored once.
        logo_src = await storage.put_branding_asset(
            s, tenant_id, "logo", data, content_type, proxy=True
        )
        icon_src = await storage.put_branding_asset(
            s, tenant_id, "icon", data, content_type, proxy=True
        )

        # 4. Into the theme, for BOTH portals: a client email and a staff email dress from
        # different blocks, and a dev box wants the logo on both.
        for portal in PORTALS:
            # THE STORED MODEL, dumped and amended — so every other field survives. Built from
            # `get_branding`'s answer instead, this wrote a Branding whose ONLY populated fields
            # were the two set here: that dict nests the real values under `branding`, pydantic
            # ignored the keys it did not know, and a seed run silently replaced a firm's colours,
            # copy, support address and portal pages with defaults. It was invisible because a
            # fresh dev tenant has defaults anyway.
            current = await branding_mod.stored_branding(s, tenant_id, portal)
            payload = current.model_dump()
            payload.update({"logo_url": logo_src, "icon_url": icon_src})
            model = branding_mod.Branding.model_validate(payload)
            await branding_mod.set_branding(s, tenant_id, portal=portal, name=None, branding=model)

    # 5. Prove it through the send path's OWN resolution rather than by reading the row back: what
    # the masthead renders is `brand_for`'s answer, so that is what a dev box needs to see.
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        # No cache poke here any more: `branding.set_branding` clears the resolved brand itself now,
        # which is what a real upload through `/branding` needed and never had. This reach-in was
        # the only thing in the repository that knew the cache existed.
        for portal in PORTALS:
            brand = await email_transport.brand_for(s, tenant_id, portal=portal)
            print(f"{portal:9} logo_url={brand.logo_url}")
            print(f"{'':9} icon_url={brand.icon_url}")

    await engine.dispose()
    print(
        f"\nBranded '{brand_name}' ({tenant_uid}): logo {logo_src}, icon {icon_src}.\n"
        "A logo_url above that is a data: URI means storage is unconfigured — the portals will "
        "show it and no mail client will."
    )


if __name__ == "__main__":
    if sys.platform == "win32":  # async psycopg can't use the default ProactorEventLoop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
