"""Generate the portals' typed permission constants from the canonical registry.

The registry (app/permissions.py) is the single source of truth. This emits a matching TS module
into @graphene/api-client so the frontends share the exact same slugs, a typed `PermissionSlug`
union, and a `can()`/`satisfies()` that mirror the API's rule (write implies read; system bypass).

Run: `moon run api:generate-permissions` (or `uv run python scripts/generate_permissions_ts.py`).
CI regenerates and fails on drift, exactly like the OpenAPI client.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make `app` importable when run as a bare script (its dir, not apps/api, is sys.path[0]).
_API_ROOT = Path(__file__).resolve().parents[1]  # apps/api
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.permissions import ALL  # noqa: E402  (after the sys.path shim above)

# Lives at the package root (NOT src/) so `openapi-ts`, which cleans its src/ output dir on every
# client regen, never clobbers it. Exposed via the package.json "./permissions" subpath export.
OUT = _API_ROOT.parent.parent / "pkgs" / "api-client" / "permissions.ts"


def _ts_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def render() -> str:
    union = "\n".join(f"  | {_ts_string(p.slug)}" for p in ALL)
    entries = "\n".join(
        f"  {_ts_string(p.slug)}: {{ slug: {_ts_string(p.slug)}, name: {_ts_string(p.name)}, "
        f"resource: {_ts_string(p.resource)}, action: {_ts_string(p.action)}, "
        f"portal: {_ts_string(p.portal)}, description: {_ts_string(p.description)} }},"
        for p in ALL
    )
    slug_list = ", ".join(_ts_string(p.slug) for p in ALL)
    header = (
        "// AUTO-GENERATED from apps/api/app/permissions.py by "
        "scripts/generate_permissions_ts.py.\n"
        "// Do not edit by hand — run `moon run api:generate-permissions` (CI fails on drift)."
    )
    return f"""{header}

export type PermissionSlug =
{union};

export type Portal = 'client' | 'platform' | 'operations';

export interface PermissionMeta {{
\tslug: PermissionSlug;
\tname: string;
\tresource: string;
\taction: string;
\tportal: Portal;
\tdescription: string;
}}

export const PERMISSIONS: Record<PermissionSlug, PermissionMeta> = {{
{entries}
}};

export const PERMISSION_SLUGS: PermissionSlug[] = [{slug_list}];

/** Does a set of held slugs satisfy a required one? `system` satisfies everything; a `:write`
 * satisfies the matching `:read` (write implies read). Mirrors app.permissions.satisfies. */
export function satisfies(held: Iterable<string>, required: string): boolean {{
\tconst set = held instanceof Set ? held : new Set(held);
\tif (set.has('system') || set.has(required)) return true;
\tif (required.endsWith(':read')) return set.has(required.slice(0, -':read'.length) + ':write');
\treturn false;
}}

/** Authorise in the UI: the `system` permission bypasses; otherwise the held permissions must
 * satisfy `required`. Mirrors the API's require_permission.
 *
 * `role` is accepted for call-site compatibility and deliberately NOT trusted. It comes straight
 * from the tenant's own WorkOS environment, so a firm that named a role "admin" used to get a
 * cross-tenant superuser bypass here — the same defect app/auth.py::is_system was fixed for. The
 * only superuser signal is the `system` permission, which a tenant cannot grant itself. */
export function can(
\tpermissions: Iterable<string>,
\t_role: string | null,
\trequired: PermissionSlug | string
): boolean {{
\treturn satisfies(permissions, required);
}}
"""


def main() -> None:
    # Force UTF-8: descriptions carry em-dashes, and write_text() defaults to the platform
    # encoding (cp1252 on Windows), which would emit invalid bytes and drift against CI.
    OUT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(ALL)} permissions)")


if __name__ == "__main__":
    main()
