"""Per-tenant models: this firm's slice of the conformed layer, staged, and merged into the book.

The same declaration Seccl and Morgan Lloyd carry — `book_sink.register` owns the shape
(filter -> stage -> merge) and this file is the provider's statement that it participates.

SEI's slice is the one that is NOT an `lineage` containment test. Its enablement is the
firm/subfirm/DSR triple (`predicate.sei`), narrowed per relation on the axes that relation actually
has, because `platform.nodes` has no `subfirm_id` and the dimension tables carry a scalar where the
entities carry a list. Switching it to containment would widen a multi-axis slice — a firm AND a
subfirm AND a DSR team becomes OR under ancestry — so that waits on migrating `tenant_platforms`
config to leaf org keys.

The tenant list is read when the project compiles. `book_sink.tenant_reload` recompiles
when a firm is enabled or removed, so a daemon restart is not required before Refresh.
"""

from __future__ import annotations

from book_sink import install_api_log, register
from book_sink.tenant_reload import install_tenant_reload

MODELS = register("sei")
install_tenant_reload("sei")

# Record this daemon's outgoing SEI calls in `audit.api_log` as source='sync'. See the same call
# in the Seccl service for why model discovery is the hook. Note the scope: this covers the SWP
# API client (`pkgs/sei`), not the SFTP drop -- `raw.files` moves bytes over a different
# transport, and `audit.api_log.transport` exists to keep the two distinguishable if it is ever
# recorded too.
install_api_log()
