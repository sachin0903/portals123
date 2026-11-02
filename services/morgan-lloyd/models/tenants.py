"""Per-tenant models: this firm's slice of the conformed layer, staged, and merged into the book.

The same declaration Seccl carries, and for the same reasons — `book_sink.register` owns the shape
(filter -> stage -> merge) and this file is the provider's statement that it participates.

What is different here is the SCOPE, and it is narrow on purpose: `book_sink` merges ML's `clients`
and nothing else yet. `transfers` and `beneficiaries` conform and project into the tenant's lake,
but their book tables are `platform.transfers` / `platform.beneficiaries`, whose lifecycle Graphene
owns — a transfer's stage is advanced by our own reconciliation, not by a nightly overwrite from
Dynamics (docs/contributions.md; the ML transfer push is unbuilt). Merging them would let a stale
vendor read walk a transfer backwards, which `0056`'s monotonic status rule exists to prevent.

The tenant list is read when the project compiles. `book_sink.tenant_reload` recompiles
when a firm is enabled or removed, so a daemon restart is not required before Refresh.
"""

from __future__ import annotations

from book_sink import install_api_log, register
from book_sink.tenant_reload import install_tenant_reload

# `morgan-lloyd`, the ONE spelling (0131). This is the provider REGISTRY key — it names the
# tenants to iterate, the `platform.provider_code` value every merged row carries, and the slice
# predicate — not the Python package, which stays `morgan_lloyd` because a hyphen is not a legal
# identifier.
MODELS = register("morgan-lloyd")
install_tenant_reload("morgan-lloyd")

# Record this daemon's outgoing Dynamics calls in `audit.api_log` as source='sync'. See the same
# call in the Seccl service for why model discovery is the hook.
install_api_log()
