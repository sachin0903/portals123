"""Scalar shell for /docs.

Guide markdown is not rendered here. Scalar 1.71.0
(``@scalar/workspace-store`` ``traverseDescription``) turns headings in the
OpenAPI ``info.description`` into top-level sidebar pages. The Clients and
Checks guides are those tags' ``description`` fields. The classic layout
renders a tag description above that tag's operations. ``mcp.disabled`` is
the installed switch that removes Generate MCP.

The browser bundle is the pinned ``@scalar/api-reference`` standalone build,
vendored beside this module and served by this process. ``/docs`` does not
load a script from another host.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

GUIDES = Path(__file__).resolve().parent / "guides_md"
SCALAR_VERSION = "1.71.0"
SCALAR_SCRIPT = "/docs/scalar-api-reference.js"
BUNDLE = Path(__file__).resolve().parent / "static" / "scalar-api-reference.js"
GUIDE_ORDER = (
    "overview",
    "authentication",
    "versioning",
    "rate-limits",
    "errors",
    "webhooks",
    "custodians",
)


def guide_markdown() -> str:
    """Top-level guides. Each file starts with a heading, which Scalar pages."""
    parts = [(GUIDES / f"{slug}.md").read_text(encoding="utf-8").strip() for slug in GUIDE_ORDER]
    return "\n\n".join(parts) + "\n"


def tag_guide(slug: str) -> str:
    """Guide markdown attached to an API tag, not concatenated into info.description."""
    return (GUIDES / f"{slug}.md").read_text(encoding="utf-8").strip() + "\n"


@lru_cache(maxsize=1)
def scalar_bundle() -> bytes:
    """Pinned Scalar standalone script. Read once; the file is a few megabytes."""
    body = BUNDLE.read_bytes()
    pinned = f"/* @scalar/api-reference@{SCALAR_VERSION} */".encode()
    if not body.startswith(pinned):
        raise RuntimeError("Scalar bundle does not match the pinned version")
    return body


def reference_html() -> str:
    return f"""<!doctype html>
<html lang="en-GB">
<head>
  <meta charset="utf-8">
  <title>Graphene API</title>
  <style>
    html, body, #app {{ margin: 0; height: 100%; }}
  </style>
</head>
<body>
  <div id="app"></div>
  <script src="{SCALAR_SCRIPT}"></script>
  <script>
    Scalar.createApiReference('#app', {{
      url: '/schema/openapi.json',
      authentication: {{ preferredSecurityScheme: 'bearerAuth' }},
      mcp: {{ disabled: true }}
    }})
  </script>
</body>
</html>"""
