# Smart (domain) template components — spec + render-context scope

Status: **spec, not yet built.** The template editor's blocks today are all generic (Text, Image,
Label+Value, callouts, Text/Data table, Chart, Repeat, Conditional). This doc specs the first batch of
_domain_ components — blocks that drop in pre-wired to a record fragment so an author never hand-tokens
a Label+Value grid again — and scopes what the higher-impact ones need from the render context.

**Coordination:** implementing these edits `apps/platform/src/lib/components/templates/editor/blocks.ts`
and `editor/component-types.ts` (+ the render side for the context additions). Those two block files are
being worked on in a parallel session — land/coordinate before implementing §2.

---

## 1. What a template can bind to today (the authoritative bind surface)

Verified against `apps/api/app/render/sample.py::sample_context` (the token catalogue the preview and the
publish/generate lint both key off) and the real generate-time assembly in `apps/api/app/worker.py`.

| Token                                                                                                      | Real source at generate                                                | Notes                                                                                                                                                                                                                                                                                                                                                                     |
| ---------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `client.name` `.first_name` `.surname` `.title` `.email` `.phone` `.telephone` `.address` `.date_of_birth` | `crm.contacts` (worker.py `_client_context`, recipient contact)        | `address` is jsonb → one line; `date_of_birth` formatted. `client.id` is internal (dataset scoping). **`.phone` reads `mobile`** — the token kept its name so an already-authored letter still fills with the mobile. **`.telephone` reads `phone`**, the other number. `client.other_numbers` is the same string, so a letter that already used it still shows a number. |
| `client_name` `client_email`                                                                               | flat aliases                                                           |                                                                                                                                                                                                                                                                                                                                                                           |
| `adviser.name` `.email`                                                                                    | `auth.users` (`generated_by`)                                          | **no** `.phone`, **no** FCA number today                                                                                                                                                                                                                                                                                                                                  |
| `firm.name`                                                                                                | `auth.tenants.name`                                                    | **no** address, and `firm.frn`/`firm.chn` are stored but NOT surfaced (see §2.0)                                                                                                                                                                                                                                                                                          |
| `today` (ISO) · `date` (`%d %B %Y`) · `document_name`                                                      | render time                                                            |                                                                                                                                                                                                                                                                                                                                                                           |
| form answers                                                                                               | each linked-form field, spread top-level **and** under `answers.<key>` | typed sample values in preview                                                                                                                                                                                                                                                                                                                                            |
| `<dataset_code>` · `datasets.<code>` · `one.<code>.<col>`                                                  | `documents.datasets` resolved at render (resolve.py)                   | the `{% for %}` / table vehicle — **OLTP only** (see §3)                                                                                                                                                                                                                                                                                                                  |

So the whole first batch (A + E) binds to `client.*`, `adviser.*`, `firm.*`, `today`/`date` — all present
today — with a single tiny addition (`firm.frn`) for the letterhead FCA line.

---

## 2. First batch — regulatory (E) + letter furniture (A)

Highest value-per-effort: mostly static/lightly-tokened, no backend plumbing. Two implementation shapes.

### 2.0 One small context addition: `firm.frn` (+ optionally `firm.chn`)

`auth.tenants.frn int` and `.chn text` exist (`migrations/sql/0001_auth_core.sql:137-138`) but are read
nowhere — `frn` is the FCA Firm Reference Number a letterhead reg line needs. No schema change: just surface
them in the two context builders.

- `apps/api/app/worker.py` `_actor_firm_context` — the firm SELECT already reads `auth.tenants`; add
  `frn, chn` and put them in the `firm` dict: `firm = {"name": ..., "frn": row.frn, "chn": row.chn}`.
- `apps/api/app/render/sample.py` — mirror it in `_SAMPLE_*`/`sample_context`: `"firm": {"name": "Sample
Advisers Ltd", "frn": "123456", "chn": "…"}` so the preview + token catalogue know the field.
- Add `firm.frn` (+ `firm.chn`) to `CORE_VARIABLES` in `TemplateEditor.svelte` so the Insert-variable
  picker offers them.

_Not in this batch (need real additions, flagged for later):_ `adviser.fca_number`/SPS number (**does not
exist** anywhere — needs schema + data), firm **address** (no source today), `client` reference / NI number
(not on `crm.contacts` — would need a `data`-jsonb read or a column), `adviser.phone`.

### 2.1 Regulatory components → new `doc-risk-warning` type with a `variant` trait

Mirror `doc-callout` (`component-types.ts:978-1045`): a `select` trait swaps the boilerplate copy + styling.
Pure boilerplate — **no tokens, no context** — so these are the cheapest and the highest-value (a generated
letter that looks properly regulated).

```ts
// component-types.ts — new type
dc.addType("doc-risk-warning", {
  isComponent: (el) =>
    el.getAttribute?.("data-gjs-type") === "doc-risk-warning"
      ? { type: "doc-risk-warning" }
      : false,
  model: {
    defaults: {
      name: "Risk warning",
      icon: LAYER_ICONS.callout, // reuse or add an icon
      warnVariant: "investment",
      unstylable: hideSectors("position", "layout"),
      traits: [
        {
          type: "select",
          label: "Warning",
          name: "warnVariant",
          changeProp: true,
          options: [
            { id: "investment", name: "Investment — capital at risk" },
            { id: "pension", name: "Pension — retirement" },
            { id: "capital", name: "Capital at risk (short)" },
            { id: "past", name: "Past performance" },
          ],
        },
      ],
    },
    init(this: any) {
      const a = this.get("attributes") ?? {};
      if (a["data-variant"]) this.set("warnVariant", a["data-variant"]);
      this.on("change:warnVariant", this.updateWarn);
    },
    updateWarn(this: any) {
      const v = this.get("warnVariant") || "investment";
      this.components(WARN_COPY[v] ?? WARN_COPY.investment); // set inner text
      this.addStyle({
        background: "#fff7ed",
        border: "1px solid #fed7aa",
        "border-radius": "6px",
        padding: "10px 12px",
        margin: "8px 0",
        "font-size": "11px",
        color: "#9a3412",
        "line-height": "1.5",
      });
    },
  },
  view: {
    onRender(this: any) {
      this.model.updateWarn();
    },
  },
});
```

`WARN_COPY` is a small map of the standard FCA-style boilerplate per variant (kept in `component-types.ts`
alongside the type). Blocks (`blocks.ts`, category `Regulatory`) each drop the type with a seed
`data-variant`: **Risk warning — investment**, **— pension**, **— capital at risk**, **Past performance**.

Same pattern (static boilerplate blocks, no new type needed — just `data-gjs-type="text"` content):

- **FSCS protection statement** — the standard FSCS wording as an editable text block.
- **Complaints / contact-us footer** — boilerplate + `branding.support` (already validated on the tenant
  theme; pass it into context the same way `firm` is, or keep the text editable).

### 2.2 Letter-furniture components → block compositions (no new type)

These are `blocks.ts` additions only — pre-wired grids of `doc-field` (Label+Value) rows + text bound to
existing tokens. Category `Letter`. Content sketches (real tokens):

- **Client identity block** — `doc-field` rows: Name → `{{ client.name }}`, Date of birth →
  `{{ client.date_of_birth }}`, Address → `{{ client.address }}`, Email → `{{ client.email }}`, Mobile →
  `{{ client.phone }}` (the primary mobile; `client.phone` is the token's unchanged name — see §1).
  (Client reference / NI number omitted until a source exists; the other numbers have no token, for
  the same reason.)
- **Recipient address block** (windowed-envelope) — stacked text: `{{ client.title }} {{ client.name }}`
  then `{{ client.address }}`.
- **Adviser details card** — `doc-field` rows: Adviser → `{{ adviser.name }}`, Email →
  `{{ adviser.email }}`. (FCA/SPS + phone omitted until sources exist.)
- **Firm letterhead / logo lockup** — the `@graphene/ui` `Logo`/brand mark + `{{ firm.name }}` + an FCA
  line **"Authorised and regulated by the FCA. Firm reference number {{ firm.frn }}."** (needs §2.0).
- **Salutation & sign-off** — `Dear {{ client.first_name }},` … `Yours sincerely,` / `{{ adviser.name }}`
  - an optional signature `image` placeholder.
- **Document meta strip** — `{{ document_name }} · {{ date }} · prepared for {{ client.name }} by
{{ adviser.name }}`.
- **Page footer** — footer band (the editor already has header/footer support) with the firm regulated
  line + page numbers.

None of these need new component types — they compose `doc-field` + `text` + `image`, all of which already
exist and stay individually editable after insertion.

---

## 3. Portfolio / projection (B / C / D) — render-context scope

These need the render context to carry the subject account / holdings / illustration. The seam is
`apps/api/app/worker.py::_load_inputs` (worker.py:252-352), which already resolves the subject
`crm.contacts.id` (`subject_id`, worker.py:283) and passes it to dataset resolution. Findings per group:

### D — money, fees, movements → **doable now via subject-scoped SQL datasets**

`platform.accounts` (OLTP) carries `fees`, `payments`, `withdrawals`, `transfer` jsonb + `value`, and
`platform.transactions` carries `isin, asset_name, amount, quantity, price, type, transaction_date`. A
`subject`-scoped SQL dataset binding `:subject_id` (= `crm.contacts.id`) reaches these live at render
(resolve.py `_resolve_sql`). So **Fees & charges**, **Contributions & withdrawals**, **Transactions
statement**, **Tax-year summary** can be built as dataset-backed table components with **no new plumbing** —
just author the datasets + a table preset that loops them. This is the cheapest of B/C/D.

### B — accounts, holdings, allocation → **needs lake→context plumbing**

`platform.accounts` (OLTP) gives an account summary (`value`, wrapper via `platform.products.product_type`,
provider, cash-vs-invested only in aggregate) reachable by dataset SQL. But **fund-level holdings**
(`fund, allocation, units, price, value, growth, twrr`) and the **valuation series** live in **DuckDB**
(`app/lake/seccl.py::_SECCL_HOLDINGS_SQL`, `seccl_value_series`) which a dataset's `SELECT` **cannot reach**
(dataset SQL runs against OLTP). Options, in order of effort:

1. **Worker-side lake read** — extend `_load_inputs` to read the lake for the subject (map `subject_id` →
   Seccl `external_id`s via `platform.platform_links`) and inject `holdings` / `account` into `context`
   (so `{% for h in holdings %}` and `{{ account.value }}` work). New but contained.
2. **Lake→OLTP materialisation** — periodically land holdings into an OLTP table a dataset can query. More
   moving parts; keeps the dataset mechanism uniform.
   The **asset-allocation donut** reuses the existing Chart bound to a holdings dataset once (1) or (2)
   exists; the classifier is `crm.py::_asset_class`.

### C — projection / illustration table → **blocked by CTC's output shape**

The CTC engine returns a **PDF, not structured data** (`integrations/ctc/xml_builder.py::IllustrationResponse`
carries only `success` / `document_location` / `errors`; the age × fund-value-at-growth-rate projection +
charges exist only _inside_ the generated PDF). So a projected-value **table** is **not buildable as data**
without a CTC engine that returns XML results. What _is_ structured is the **request** side
(`IllustrationRequest`: funds, `rate_of_return`, `management_charge`, `initial_charge`, contribution/
withdrawal) — so an **"Assumptions & charges"** component (what goes _in_) is feasible now; the projected
values coming back are not. Flag for the CTC integration owner: to get a projection table, the engine call
needs a results-XML mode (not just the Bespoke PDF).

---

## 4. Suggested sequencing

1. **E (regulatory) + the `firm.frn` addition + A (letter furniture)** — cheapest, no plumbing, and what
   makes generated letters look professionally regulated. Ships behind the block-file coordination only.
2. **D (fees / movements / transactions)** — dataset-backed, no new render plumbing; author the
   subject-scoped datasets + table presets.
3. **B (accounts / holdings / allocation)** — needs the worker-side lake read (option 1). Highest visual
   impact once the plumbing lands.
4. **C (projection)** — the "Assumptions & charges" part now; the projection table waits on a CTC
   results-XML mode.
