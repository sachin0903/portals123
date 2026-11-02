"""Create this provider's own views on a freshly-opened lake connection.

The `.sql` files under `views/` hold the joins against `core.platform.*`/`core.raw.*` (`core`
being the read-only attached catalogue `shared/lake.py` sets up) — the parts of each query that
don't depend on a call's own firm/dsr/date, so they're defined once here rather than repeated
inline in every `providers/intelliflo/db.py` function. Each view is created in the connection's
own default catalog (`memory`, explicitly — see the `.sql` files themselves), since `core` is
READ_ONLY and creating anything there would fail.
"""

from pathlib import Path

_VIEWS_DIR = Path(__file__).parent / "views"


def create_views(con) -> None:
    """Run every `views/*.sql` file against `con`. Safe to call on every `open_lake()` connection
    — each file is its own `CREATE OR REPLACE VIEW`, so this is idempotent per connection."""
    for path in sorted(_VIEWS_DIR.glob("*.sql")):
        con.execute(path.read_text())
