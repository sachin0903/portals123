"""Move a branding or content image onto the relay path, so email and the portals can both use it.

WHY A DEPLOYMENT ENDS UP HERE. ``storage.put_branding_asset`` has three rungs: the tenant's own
bucket, the legacy shared bucket, and — with neither configured — a base64 ``data:`` URI stored in
``auth.tenants.theme``. That last rung is deliberate and it is not a failure: a firm uploads its
logo, the portals render it, and nothing is lost. But it is INVISIBLE, and two things follow that
nobody is told about at upload time:

* **It can never appear in an email.** ``email/transport.py::_https_only`` refuses a ``data:`` URI
  on purpose (mail clients block them), so ``brand.logo_url`` resolves to None and the masthead
  falls back to the firm's name set in type. The firm sees its logo everywhere on screen and never
  in an inbox, which reads as "the email logo feature is broken".
* **It is served on the branding hot path.** ``environments.env_config_by_host`` returns the theme
  on every page load and the portals inject it into the SSR head. A 265 kB logo — measured on a
  real deployment — is 265 kB per page, per portal, per visitor.

Configuring object storage afterwards fixes NEITHER, because the stored value is already a data
URI and nothing rewrites it: the firm would have to notice and re-upload. This script is that
rewrite. For every tenant (or one, by alias), each of the four branding images that is inlined is
decoded, stored through the SAME call an upload makes (``put_branding_asset(..., proxy=True)``) and
the theme is re-saved through ``set_branding``, so the value becomes the ordinary
``/_assets/{name}`` relay path and the email path can inline it as a ``cid:`` attachment.

IT REFUSES RATHER THAN REWRITING A DATA URI AS A DATA URI. If storage is still unconfigured,
``put_branding_asset`` returns another inline value; writing that back would report success, change
nothing and cost a version of the theme. So the first tenant that comes back inlined stops the run
and says what to configure.

Run:  PYTHONPATH=. uv run --directory apps/api python scripts/restore_inline_branding.py
Env:  TENANT_ALIAS (one tenant; default: every tenant), DRY_RUN=1 to report without writing.
"""

import asyncio
import base64
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.getcwd())

from app import branding as branding_mod  # noqa: E402
from app import shell_images, storage  # noqa: E402
from app.config import settings  # noqa: E402

#: The branding fields that hold an image, with the upload `kind` each one is stored under. Ordered
#: so the logo — the field email reads — is done first and reported first.
ASSETS: tuple[tuple[str, str], ...] = (
    ("logo_url", "logo"),
    ("icon_url", "icon"),
    ("logo_dark_url", "logo-dark"),
    ("favicon_url", "favicon"),
)

PORTALS: tuple[branding_mod.Portal, ...] = ("client", "platform")
DRY_RUN = os.getenv("DRY_RUN") == "1"
ONLY_ALIAS = os.getenv("TENANT_ALIAS") or None


def decode_data_uri(value: str) -> tuple[bytes, str] | None:
    """``(bytes, content_type)`` for a base64 ``data:`` URI, or None for anything else.

    Base64 only, which is what `put_branding_asset` writes. A percent-encoded `data:` URI is a
    valid URI and is NOT something this estate produces, so decoding one would be inventing a
    migration for a value no writer here creates.
    """
    if not value.startswith("data:") or ";base64," not in value:
        return None
    header, _, payload = value.partition(";base64,")
    content_type = header[len("data:") :]
    if not content_type or not payload:
        return None
    try:
        return base64.b64decode(payload, validate=True), content_type
    except (ValueError, TypeError):
        return None


def relay_path_for(value: str, public_base: str | None) -> str | None:
    """The relay path for a value that is already an ABSOLUTE object-store URL, or None.

    THE SECOND SHAPE, and on a container deployment the worse one. Before every upload path passed
    ``proxy=True`` the store returned ``{public_base_url}/{key}`` — and a tenant provisioned inside
    docker compose has ``public_base_url = http://rustfs:9000/t-{uid}``, the INTERNAL service name.
    That URL is not reachable from any browser outside the compose network (so the image is broken
    in the portal), it is not https (so ``_https_only`` refuses it for email), and it publishes the
    object store's own address into markup, which the relay exists to avoid.

    NOTHING IS RE-UPLOADED for this shape: the object is already in the bucket under that exact key,
    so the fix is to stop naming it by a host at all. The caller verifies the object is readable
    before writing the new value — a rewrite that pointed at nothing would turn a broken image into
    a different broken image and look like progress.
    """
    base = (public_base or "").rstrip("/")
    if not base or not value.startswith(f"{base}/"):
        return None
    key = value[len(base) + 1 :]
    if not key.startswith(storage.TENANT_PUBLIC_PREFIX):
        return None
    name = key.rsplit("/", 1)[-1]
    # VALIDATE THE PATH, NOT THE NAME. `public_asset_name` parses a SRC — it expects the
    # `/_assets/` prefix and returns None for a bare filename — so checking the name alone refused
    # every value and the migration silently did nothing while reporting a clean run. Build the
    # candidate and ask whether the relay would serve THAT, which is the actual question: a name
    # outside the grammar is a value nothing can serve, so it is skipped and the caller reports it.
    candidate = f"{storage.PUBLIC_ASSET_PREFIX}{name}"
    return candidate if storage.public_asset_name(candidate) else None


async def main() -> int:
    engine = create_async_engine(settings.migration_database_url or settings.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    moved = inlined = skipped = 0

    async with maker() as s, s.begin():
        # `service_role`, because `auth.tenants` is FORCE-RLS and a plain session carries no claims:
        # it would see no tenants at all and report a clean run over an untouched database.
        await s.execute(text("SET LOCAL ROLE service_role"))
        # The filter is built rather than parameterised on a NULL: Postgres cannot infer the type of
        # a bare `$1 IS NULL` and refuses the statement outright (`AmbiguousParameter`). A cast would
        # work; two statements read better than `:alias::text IS NULL`.
        rows = (
            await s.execute(
                text("SELECT id, uid, alias, name FROM auth.tenants WHERE alias = :alias"),
                {"alias": ONLY_ALIAS},
            )
            if ONLY_ALIAS
            else await s.execute(text("SELECT id, uid, alias, name FROM auth.tenants ORDER BY id"))
        ).all()
        if not rows:
            print(f"no tenant matched (alias={ONLY_ALIAS!r})")
            await engine.dispose()
            return 1

        for tenant in rows:
            for portal in PORTALS:
                current = await branding_mod.stored_branding(s, tenant.id, portal)
                changes: dict[str, str] = {}
                for field, kind in ASSETS:
                    value = getattr(current, field, None) or ""
                    decoded = decode_data_uri(value)
                    if decoded is None:
                        if value:
                            skipped += 1
                        continue
                    data, content_type = decoded
                    print(
                        f"{tenant.name} [{portal}] {field}: {len(data):,} bytes inline "
                        f"({content_type})"
                    )
                    # COUNTED BEFORE THE DRY-RUN RETURN. Tallied after it, a dry run listed four
                    # inline images and then closed with "0 images; nothing was inlined" — the
                    # summary contradicting the lines above it, which is worse than no summary.
                    inlined += 1
                    if DRY_RUN:
                        continue
                    # THE SAME DERIVATIVE AN UPLOAD WOULD STORE. The inlined value is exactly the
                    # shape this exists to retire — on staging a 3481×624 PNG the shell painted at
                    # 28px — and moving it onto the relay at full size would keep every page
                    # downloading and decoding a bitmap it shows at a hundredth of the size.
                    try:
                        data, content_type = await shell_images.fit_shell_upload(
                            kind, data, content_type
                        )
                    except ValueError as exc:
                        print(f"  {field}: skipped, {exc}")
                        skipped += 1
                        continue
                    src = await storage.put_branding_asset(
                        s, tenant.id, kind, data, content_type, proxy=True
                    )
                    if src.startswith("data:"):
                        # Storage is still unconfigured. Stop rather than write the same value back.
                        print(
                            "\nSTOPPED — object storage is not configured on this deployment, so "
                            "the upload came back inline again.\n"
                            "Set the S3 credentials (S3_ENDPOINT_URL / S3_ACCESS_KEY_ID / "
                            "S3_SECRET_ACCESS_KEY), provision the tenant buckets "
                            "(POST /admin/storage/provision-all) and run this again."
                        )
                        await engine.dispose()
                        return 2
                    changes[field] = src

                if changes and not DRY_RUN:
                    payload = current.model_dump()
                    payload.update(changes)
                    model = branding_mod.Branding.model_validate(payload)
                    # THE STORED MODEL, amended — never a fresh `Branding` carrying only these two
                    # keys, which would replace the firm's colours, copy and pages with defaults.
                    await branding_mod.set_branding(
                        s, tenant.id, portal=portal, name=None, branding=model
                    )
                    for field, src in changes.items():
                        print(f"  {field} -> {src}")
                    moved += 1

            # ── CONTENT, the same two shapes on a different table ──────────────────────────────
            # The library's hero images go through the SAME `put_branding_asset(..., proxy=True)`
            # call, so a row written before that flag existed holds an absolute store URL. The hero
            # is a COLUMN on `content.items` and is not versioned, so it can simply be rewritten;
            # a published version's `body_html` cannot (`content_item_versions_immutable`), so any
            # occurrence there is reported and left for a republish.
            public_base = (
                await s.execute(
                    text("SELECT public_base_url FROM storage.tenants WHERE tenant_id = :t"),
                    {"t": tenant.id},
                )
            ).scalar_one_or_none()
            items = (
                await s.execute(
                    text(
                        "SELECT id, uid, title, hero_url FROM content.items "
                        "WHERE tenant_id = :t AND hero_url IS NOT NULL AND hero_url <> ''"
                    ),
                    {"t": tenant.id},
                )
            ).all()
            for item in items:
                relay = relay_path_for(item.hero_url, public_base)
                if relay is None:
                    if item.hero_url.startswith("data:"):
                        print(f"{tenant.name} content '{item.title}': hero is INLINE — not moved")
                        print("  (the hero is uploaded through the portal; re-upload it there)")
                    continue
                name = relay.removeprefix(storage.PUBLIC_ASSET_PREFIX)
                try:
                    await storage.get_public_asset(s, tenant.id, name)
                except Exception as exc:  # noqa: BLE001 — any read failure means do not rewrite
                    print(f"{tenant.name} content '{item.title}': {name} unreadable ({exc}) — left")
                    continue
                print(f"{tenant.name} content '{item.title}': hero {item.hero_url}")
                inlined += 1
                if DRY_RUN:
                    continue
                await s.execute(
                    text("UPDATE content.items SET hero_url = :u WHERE id = :i"),
                    {"u": relay, "i": item.id},
                )
                print(f"  hero_url -> {relay}")
                moved += 1

            stuck = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM content.item_versions "
                        "WHERE tenant_id = :t AND (body_html LIKE 'data:%' "
                        "   OR position('src=\"http' in body_html) > 0)"
                    ),
                    {"t": tenant.id},
                )
            ).scalar_one()
            if stuck:
                print(
                    f"{tenant.name}: {stuck} PUBLISHED content version(s) carry an absolute or "
                    "inline image. Those rows are insert-only by trigger — republish the item to "
                    "pick up the corrected draft."
                )

    await engine.dispose()
    verb = "would move" if DRY_RUN else "moved"
    print(
        f"\n{verb} {inlined} image(s) in {moved} record(s); {skipped} already on a usable source."
    )
    if not inlined:
        print("Nothing to move — every branding and content image is already a relay path.")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
