"""Per-tenant models: this firm's slice of the conformed layer, staged, and merged into the book.

The models are registered dynamically, one set per tenant enabled for Seccl in
`platform.tenant_platforms`, because the tenants are data and the DAG is not. `book_sink.register`
owns the shape (filter -> stage -> merge) and documents why each layer exists; this file is the
provider's declaration that it participates.

The tenant list is read when the project compiles. `book_sink.tenant_reload` compares it
with the compiled tags on each daemon request and recompiles when a firm has been enabled
or removed, so Refresh of a firm just attached runs that firm's models. A tag that still
matches nothing — no active bucket, or the firm is not enabled — is a 400 from interlace.
"""

from __future__ import annotations

from book_sink import install_api_log, register
from book_sink.tenant_reload import install_tenant_reload

MODELS = register("seccl")
install_tenant_reload("seccl")

# Record this daemon's outgoing Seccl calls in `audit.api_log` as source='sync'.
#
# Here because every invocation imports `models/` — `interlace plan`, `interlace run`, and the
# daemon (`python -m book_sink.startup_run`, which then calls `interlace serve`). There is no
# separate service lifespan to hook. The same reason the tenant list above is read at import.
#
# It installs a PROCESS-WIDE default recorder, which is what makes `SecclClient()` -- constructed
# bare in a dozen places across models/ -- record without each site being changed. No DSN means
# no recorder and no thread, so `interlace plan` on a laptop is unaffected.
install_api_log()
