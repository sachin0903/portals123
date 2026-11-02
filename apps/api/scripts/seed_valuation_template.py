"""Seed a working VALUATION STATEMENT template (dev only).

A template that actually renders, so the catalogue can be exercised end to end without authoring
one by hand in the editor. It draws on ``book_valuations`` — the provided per-client dataset — and
is published, so ``POST /documents/generate`` can be pointed at it immediately.

TWO THINGS MAKE IT VALID, and both are things a hand-written template gets wrong:

* **The dataset is bound by its BARE code.** A template binds a dataset by writing
  ``{% for row in <code> %}``, and ``render/components.py::loop_sources`` deliberately ignores a
  DOTTED source (``{% for a in answers.items %}`` is a path into the context, not a dataset name).
  A namespaced code is therefore never discovered, never resolved, and Jinja renders the whole
  table blank — so the loop below, ``linked_datasets`` and the chart's ``_resolved_data`` all say
  ``book_valuations``.
* **Every cell is guarded.** ``book_valuations`` publishes nullable columns (``cash``, ``cost``,
  ``gain`` are NULL for an account the provider values without them), and an unguarded
  ``{{ row.cash | currency }}`` renders "£0.00" for "we do not know", which is a different claim.
  Each optional figure is ``{% if %}``-ed to an em dash instead.

``linked_datasets`` is set as well as the loop being present. Either alone would resolve — the
declared link and the discovered loop source are unioned (``worker.py``) — but the declared one is
the contract, and a template whose only binding is a loop inside its markup loses the dataset the
moment somebody edits the table out.

Run:  uv run --directory apps/api python scripts/seed_valuation_template.py [tenant_alias]
Requires: migrations applied, and a tenant with at least one user to attribute the seed to.
"""

import asyncio
import json
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.ids import new_id

DEFAULT_TENANT_ALIAS = "graphene"

CODE = "valuation_statement"
DATASET = "book_valuations"

# The document body. Plain HTML + Jinja: a data table IS a `{% for %}` loop (the editor's
# `doc-jtable`), and needs no render-pipeline handling beyond the dataset being resolved.
#
# `client` and `date` come from the render CONTEXT, not the dataset — and they are the only two
# context variables used, checked against what `worker.py` actually builds rather than assumed.
# There is deliberately no "as at <date>" heading: the worker publishes `today`/`date` (when the
# document was prepared) and NOT an as-at, and each row already carries its own `valued_on`, which
# is the honest date for that account. A single as-at over rows valued on different days would
# misstate some of them.
HTML = """
<div class="doc">
  <header class="head">
    <h1>Valuation statement</h1>
    <p class="sub">{{ client.name }}</p>
    <p class="asat">Prepared {{ date }}</p>
  </header>

  <!-- The chart's MARKER. `render/components.py::substitute_components` replaces
       `<g-chart data-id="...">` with server-rendered SVG (WeasyPrint runs no JS), matching it to the
       component by `data-id`. A component declared in `components` with no marker here is simply
       never placed — it renders nothing, silently. -->
  <div class="chart">
    <g-chart data-id="comp_valuation_bars" data-width="600" data-height="260"></g-chart>
  </div>

  <table class="doc-jtable-inner">
    <thead>
      <tr>
        <th>Account</th>
        <th>Product</th>
        <th>Valued on</th>
        <th class="num">Value</th>
        <th class="num">Cash</th>
        <th class="num">Cost</th>
        <th class="num">Gain</th>
      </tr>
    </thead>
    <tbody>
      {% for row in book_valuations %}
      <tr>
        <td>{{ row.account_name }}</td>
        <td>{{ row.product }}</td>
        <td>{{ row.valued_on | date }}</td>
        <td class="num">{{ row.value | currency }}</td>
        <td class="num">{% if row.cash %}{{ row.cash | currency }}{% else %}&mdash;{% endif %}</td>
        <td class="num">{% if row.cost %}{{ row.cost | currency }}{% else %}&mdash;{% endif %}</td>
        <td class="num">{% if row.gain %}{{ row.gain | currency }}{% else %}&mdash;{% endif %}</td>
      </tr>
      {% else %}
      <tr>
        <td colspan="7" class="empty">
          No valuations are held for this client over the period selected.
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <p class="note">
    Each account is shown at its most recent valuation. A provider's series can end on different
    days for different accounts, which is why every row carries its own valued-on date rather than
    this statement carrying one date for all of them.
  </p>
</div>
""".strip()

CSS = """
.doc { font-family: Helvetica, Arial, sans-serif; color: #1f2933; font-size: 10pt; }
.head { border-bottom: 2px solid #1f2933; padding-bottom: 8pt; margin-bottom: 14pt; }
.head h1 { font-size: 18pt; margin: 0 0 2pt; }
.head .sub { margin: 0; font-size: 12pt; }
.head .asat { margin: 2pt 0 0; color: #62707d; font-size: 9pt; }
table.doc-jtable-inner { width: 100%; border-collapse: collapse; }
.doc-jtable-inner th, .doc-jtable-inner td {
  border-bottom: 1px solid #e1e5ea; padding: 5pt 6pt; text-align: left;
}
.doc-jtable-inner th { background: #f5f7f9; font-size: 8.5pt; text-transform: uppercase; }
/* Right-aligned and tabular, because a column of figures is compared down its decimal point. */
.doc-jtable-inner .num { text-align: right; font-variant-numeric: tabular-nums; }
.doc-jtable-inner .empty { color: #62707d; font-style: italic; text-align: center; }
.chart { margin: 0 0 14pt; }
.note { margin-top: 14pt; color: #62707d; font-size: 8pt; line-height: 1.4; }
""".strip()

#: A chart over the same rows. `_resolved_data` is where `render/components.py::dataset_codes`
#: reads the dataset a component binds, so it carries the bare code too.
COMPONENTS = [
    {
        "id": "comp_valuation_bars",
        "type": "bar_chart",
        "props": {
            "title": "Value by account",
            "x_label": "",
            "y_label": "",
            "show_grid": True,
            "show_legend": False,
            "line_width": 1.5,
        },
        "binding": {
            "x_field": "account_name",
            "y_field": "value",
            "_resolved_data": f"{{{{ {DATASET} }}}}",
        },
    }
]

PAGE_SIZE = {"width": "210mm", "height": "297mm"}

SCHEMA = {
    "html": HTML,
    "css": CSS,
    "pageSize": PAGE_SIZE,
    "components": COMPONENTS,
    "datasets": [],
    "linkedDatasets": [DATASET],
    "layout": {"size": "A4", "orientation": "portrait", "margins": "20mm"},
    "schema": {
        "loops": [{"source": DATASET, "alias": "row"}],
        "layout": {},
        "colours": {},
        "footers": [],
        "headers": [],
        "pageSize": PAGE_SIZE,
        "variables": ["client.name", "date"],
    },
    "gjs_project": None,
}


async def main(tenant_alias: str) -> None:
    engine = create_async_engine(settings.migration_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))  # BYPASSRLS for seeding

        row = (
            await s.execute(
                text("SELECT id FROM auth.tenants WHERE alias = :a"), {"a": tenant_alias}
            )
        ).one_or_none()
        if row is None:
            raise SystemExit(f"No tenant with alias '{tenant_alias}'")
        tenant_id = int(row[0])
        actor = (
            await s.execute(
                text("SELECT id FROM auth.users WHERE tenant_id = :t ORDER BY id LIMIT 1"),
                {"t": tenant_id},
            )
        ).scalar_one_or_none()
        if actor is None:
            raise SystemExit(f"Tenant '{tenant_alias}' has no users to attribute the seed to.")

        # Idempotent, and it must DELETE rather than update: a published version's content is
        # immutable (`documents.reject_published_version_edit`), so re-seeding replaces the
        # template rather than editing what is already published.
        await s.execute(
            text("DELETE FROM documents.templates WHERE tenant_id = :t AND code = :c"),
            {"t": tenant_id, "c": CODE},
        )

        template_id = (
            await s.execute(
                text(
                    "INSERT INTO documents.templates "
                    "  (uid, tenant_id, code, name, document_type, linked_datasets, created_by) "
                    "VALUES (:uid, :t, :c, :n, 'statement', ARRAY[:ds], :by) RETURNING id"
                ),
                {
                    "uid": new_id(),
                    "t": tenant_id,
                    "c": CODE,
                    "n": "Valuation statement",
                    "ds": DATASET,
                    "by": actor,
                },
            )
        ).scalar_one()

        await s.execute(
            text(
                "INSERT INTO documents.template_versions "
                "  (uid, tenant_id, template_id, version_number, status, renderer, schema, "
                "   created_by, published_at) "
                "VALUES (:uid, :t, :tpl, 1, 'published', 'weasy', CAST(:schema AS jsonb), :by, "
                "  now())"
            ),
            {
                "uid": new_id(),
                "t": tenant_id,
                "tpl": template_id,
                "schema": json.dumps(SCHEMA),
                "by": actor,
            },
        )

    await engine.dispose()
    print(
        f"Seeded template '{CODE}' (published v1) into tenant '{tenant_alias}' "
        f"(id {tenant_id}), bound to {DATASET}."
    )


if __name__ == "__main__":
    if sys.platform == "win32":  # async psycopg can't use the default ProactorEventLoop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    alias = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TENANT_ALIAS
    asyncio.run(main(alias))
