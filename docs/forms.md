# Portal — Forms & Dynamic Fields

Status: design spec for migrating platform-1's **Fields** system into the portals monorepo as a Svelte form-builder plus a dynamic form renderer. The original pass built **only the frontend, against mock data**; since then the **forms persistence + API has landed** (migration `0011_forms`, the `/forms` router, `forms:read`/`forms:write` permissions) and the **platform builder is wired to it** — the client fill flow still resolves its definition from the bundled mock (a client-safe render path is the remaining follow-up). Template wiring (a form feeding a questionnaire dataset) stays deferred. See §11 for exactly what is and isn't built.

Scope: a staff-only feature in the **platform portal** (`apps/platform`, `:5175`). It lets firm staff author forms (a set of typed, ordered, conditional fields) and renders those forms dynamically for fill-in. There is no client-portal self-fill and no dependency on `0002_platform_crm`.

A working reference exists in platform-1: the framework-agnostic `@repo/fields` package (definitions + runtime), the `settings/fields` builder UI, and the `@repo/onboarding` dynamic-field renderer. This is a **rebuild** in the portal's stack (SvelteKit 2 / Svelte 5 runes / `@graphene/ui`), the same way the GrapesJS template editor was rebuilt. §2 says what lifts verbatim and what gets rewritten.

---

## 0. Foundation reconciliation — what is and isn't being migrated

Two things must be clear before anything else, or this spec will be misread as re-migrating work already done.

- **platform-1 has TWO builders. Only one is migrated here.** The GrapesJS document/template builder (`components/templates/template-editor.tsx`) is **already rebuilt** in the portal as the SvelteKit template editor (`apps/platform/src/lib/components/templates/TemplateEditor.svelte` + its `editor/*` util folder). This spec does **not** re-migrate it and does not touch it. The thing being migrated is platform-1's **separate "Fields" system**: `pkgs/fields` (definitions + runtime), the `apps/platform/components/settings/fields/*` builder UI, and `pkgs/onboarding/src/dynamic-fields/*` (the renderer). These are unrelated codebases in platform-1 that happen to both be "authoring" surfaces; keep them mentally separate.

- **This pass builds ONLY the Svelte builder + renderer, wired to mock data.** It mirrors exactly how the portal's template editor currently works: it persists to `localStorage` and reads seed data from `$lib/demo.ts`, with **no backend**. Explicitly **deferred** to a later pass: the entity/data-model, the SQL migration, RLS policies, FastAPI endpoints, `@graphene/api-client` generation, and the wiring of a form's output into the template-generation pipeline. Those are **documented** below (§3 shapes, §9 connection) so the mock data shapes match the eventual API — the way `apps/platform/src/lib/demo.ts` already states its intent: _"Shapes mirror the planned document-management API so wiring real data later is a swap, not a rewrite."_

- **No new portal APIs are asserted to exist.** The template/dataset/document backend is itself unbuilt (all mock in `$lib/demo.ts`). Forms follows the same rule: the immediate deliverable is strictly frontend + mock. Any endpoint named below is a _future_ shape, not a live route.

- **Ties to `0001` are forward-looking only.** Because nothing hits the DB in this pass, the `0001_auth_core` conventions (`int` tenant key, `int` PK + `text uid`, claim-driven RLS via `auth.tenant_lineage()`, person refs as `uuid`) matter only as the shape the mock records are drawn to resemble (§3, §9), so a later migration is a swap not a rewrite. The one live `0001` touchpoint is the route auth guard — pages gate on `locals.user` and UI permissions via `can(...)` exactly like `templates`/`datasets` (§5, §8).

---

## 1. The source system in platform-1

Four pieces, in two packages plus one app folder.

**`pkgs/fields` — definitions + runtime.** `src/index.ts` re-exports `service` + `types`; `src/runtime/index.ts` re-exports `visibility`/`options`/`format`. The runtime is framework- and DB-agnostic; `service.ts` is the only DB-coupled file (Drizzle over `@repo/db`).

- `types.ts` — the `Field` domain model (numeric `id`, nullable scope ids `tenantId`/`productId`/`platformId`, `entityType` in `applicant|account`, `fieldKey`, free-string `fieldType`, `label`, `section`, `description`, `required`, `conditions:unknown`, `validation:unknown`, `options:FieldOptions`, `sortOrder`). `FieldOptions` is a union of string[], `{value,label?}[]`, `{code,name?,adjective?}[]`, `{options:[…]}`, a `{source,valueKey?,labelKey?}` data-source ref, and `{currency?}`.
- `runtime/visibility.ts` — `getNestedValue(obj, path, applicationData?)` (dotted path with `application.`/`applicant.`/`account.` prefix handling) and `isFieldVisible(field, data, applicationData?)`, which reads `conditions` (preferred) else `validation.conditional`, evaluating a single `{field,operator,value}` or a `{logic,conditions[]}` group. Eight operators: `equals`/`notEquals`/`in`/`notIn`/`contains`/`notContains`/`isEmpty`/`isNotEmpty`.
- `runtime/options.ts` — `resolveOptions(field, dataSources)` → `{value:string,label:string}[]`, normalising every option shape and resolving `{source}` refs against `dataSources` (with a `nationalities` special-case).
- `runtime/format.ts` — `formatFieldValue({value,field,dataSources})` → display string (currency/percentage/date/select-label/multi-join/Yes-No), and `formatDynamicFieldsForDisplay(...)`.
- `service.ts` — CRUD + `resolveFields(input)`: queries the specificity tuples for an `entityType`, then merges by `fieldKey` keeping the higher `specificityScore` (tenant 100 > product 10 > platform 1), first-seen-wins on ties.

**`apps/platform/components/settings/fields/*` — the builder UI.** `FieldsTable` (TanStack table: list, search, drag-reorder, row menu Edit/Delete) opens a right-side **Sheet** containing `FieldsForm` (react-hook-form + Zod). Two structured sub-editors, `ConditionsEditor` and `ValidationEditor`, each with a Builder tab and a raw-JSON tab; options is a plain JSON textarea. Form-level Zod (`lib/models/fields.ts`) enforces required fields, JSON-parseable blobs, options-required-for-select-types, and "keep visibility in conditions not validation".

**`pkgs/onboarding/src/dynamic-fields/*` — the renderer.** `DynamicFieldRenderer` maps `fieldType` → a control (inline or a specialised masked input), gates on `isFieldVisible`, and derives RHF `rules` from `field.validation`. `DynamicFieldsSection` groups by section, tracks visibility, and clears/unregisters fields that transition hidden. `DynamicFieldsDisplay` is the read-only counterpart via `formatFieldValue`. Specialised inputs (`MoneyInput`, `PercentageInput`, `DateInput`, `PhoneNumberInput`, `SortCodeInput`, `BankAccountNumberInput`, `EmailInput`, `ComboboxInput`, `MultiSelectInput`, `TextareaWithMinCount`) live in `pkgs/components/src/forms/{,masked/}` with pure string transforms in `masked/utils.ts`.

**`apps/platform/lib/schemas/standard-fields.ts` — a related but distinct library.** A predefined-variable picker (31 fields in 6 categories: Client/Dates/Adviser/Financial/Rates/Document) used by the _template_ schema-builder, typed `string|number|integer`. It belongs to templates, not Fields, but is worth porting alongside as a "standard fields" seed for the form builder's field-key suggestions (§4).

---

## 2. What lifts, what gets rebuilt

| Concern                                           | platform-1                                                                      | Portal                                                                                                                |
| ------------------------------------------------- | ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Runtime (visibility, options, format, nested-get) | `pkgs/fields/src/runtime/*` — pure TS, only imports `type {Field,FieldOptions}` | **Lift verbatim** as plain TS + vitest                                                                                |
| Field/Form types                                  | `pkgs/fields/src/types.ts` (Drizzle-shaped)                                     | Rebuild as portal mock types (`uid` + `int`-ish shapes, §3)                                                           |
| `resolveFields` specificity merge                 | `service.ts` (Drizzle)                                                          | **Defer** (multi-scope resolution is a backend concern); keep `specificityScore`/merge as pure TS for the future pass |
| Builder table + form                              | React + TanStack + react-hook-form + dnd-kit                                    | Rebuild in Svelte 5 runes + `@graphene/ui` (no react-hook-form, no dnd-kit)                                           |
| ConditionsEditor / ValidationEditor               | React tabs (Builder + JSON)                                                     | Rebuild in Svelte; same emitted JSON shapes                                                                           |
| Renderer + section wrapper                        | React + RHF `Controller`/`useWatch`                                             | Rebuild in Svelte 5 runes; local `$state` form store, `$derived` visibility                                           |
| Masked inputs                                     | `pkgs/components/src/forms/masked/*`                                            | Rebuild as Svelte components; **lift `masked/utils.ts` transforms verbatim**                                          |
| Persistence                                       | Postgres via Drizzle + `/api/fields*`                                           | Mock: `localStorage` + `$lib/demo.ts` seed (§7)                                                                       |

The load-bearing rule: **the runtime is a copy, the UI is a rewrite.** `visibility.ts`, `options.ts`, `format.ts` and the `masked/utils.ts` string transforms are pure functions with no React and no DB — they port character-for-character and carry their platform-1 unit tests (`__tests__/visibility.test.ts`, `options.test.ts`, `format.test.ts`) across as vitest. Everything that touches React, RHF, dnd-kit or Drizzle is rebuilt.

Svelte-specific rebuild notes:

- **No react-hook-form.** A form's values are one `$state` record keyed by `fieldKey`; errors are a derived/imperative `$state` record. `useWatch({control})` becomes reading the values record directly; `Controller` render-props become normal `bind:value` / `onchange` on `@graphene/ui` inputs.
- **No dnd-kit.** Field reorder uses native HTML5 drag-and-drop (or a small pointer-based reorder), writing `sortOrder = index`. The portal template editor already reorders pages without dnd-kit; match it.
- **`DynamicFieldsSection`'s hidden-field cleanup effect** (clear error + unregister on visible→hidden transition, guarded so it fires only on change) becomes a `$effect` that diffs the current visible-key set against a `previous` `$state` set and deletes the newly-hidden keys' values/errors. Guard on change to avoid a runes update loop — the platform-1 comment warning about infinite rerenders applies equally.

---

## 3. Data model (mock TS types)

These are the mock shapes, declared to mirror an eventual `documents.*`-style API the way `$lib/demo.ts` mirrors document-management. They keep a public `uid text` + a human `code`, `createdAt`/`createdBy` strings, and no numeric DB ids on the wire — the same conventions the `Template`/`Dataset` mock types use in `pkgs/ui/src/lib/templates/types.ts`.

A **Form** (a.k.a. Questionnaire) is the new top-level record platform-1 lacked — platform-1 had a flat global field registry keyed by `entityType`; the portal groups fields into named, ownable forms so a form's output can become a questionnaire dataset (§9).

```ts
// pkgs/ui/src/lib/forms/types.ts  (shared, re-exported via @graphene/ui, then $lib/demo)

export type FormFieldType =
  | "text"
  | "textarea"
  | "number"
  | "checkbox"
  | "date"
  | "dateOfBirth"
  | "email"
  | "phoneNumber"
  | "bankAccountNumber"
  | "sortCode"
  | "currency"
  | "percentage"
  | "select"
  | "combobox"
  | "multiSelect" // 15 — the platform-1 set (§4)
  | "beneficiaries" // repeatable; no platform-1 equivalent
  | "phoneNumbers"; // repeatable; no platform-1 equivalent (§4.1)

export type ConditionOperator =
  | "equals"
  | "notEquals"
  | "in"
  | "notIn"
  | "contains"
  | "notContains"
  | "isEmpty"
  | "isNotEmpty";

export interface SimpleCondition {
  field: string;
  operator: ConditionOperator;
  value?: unknown;
}
export interface ConditionGroup {
  logic: "AND" | "OR";
  conditions: SimpleCondition[];
}
// Persisted (matches platform-1 exactly): a wrapper around a single condition or a group.
export type FieldConditions = {
  conditional: SimpleCondition | ConditionGroup;
} | null;

// Flat rules bag, matches platform-1 ValidationJson. Only `conditional` is honoured for
// visibility fallback; the rest drive validation (§6).
export interface FieldValidation {
  placeholder?: string;
  valueType?: "string" | "number" | "boolean";
  minLength?: number;
  maxLength?: number;
  rows?: number;
  min?: number;
  max?: number;
  pattern?: string;
  patternMessage?: string;
  errorMessage?: string;
  conditional?: SimpleCondition | ConditionGroup; // legacy-only; builder pushes into conditions
}

// Union mirrors platform-1 FieldOptions verbatim.
export type FieldOptions =
  | null
  | string[]
  | Array<
      | string
      | { value: unknown; label?: unknown }
      | { code: unknown; name?: unknown; adjective?: unknown }
    >
  | { options: unknown }
  | { source: string; valueKey?: string; labelKey?: string }
  | { currency?: string };

export interface FormField {
  uid: string; // public id (was numeric Field.id)
  fieldKey: string; // the value/answer key
  fieldType: FormFieldType | string; // string-open like platform-1 (legacy types tolerated)
  label: string;
  section: string | null;
  description: string | null;
  required: boolean;
  conditions: FieldConditions; // preferred visibility source
  validation: FieldValidation | null;
  options: FieldOptions;
  sortOrder: number;
  // Future scope axes, present but unused in this pass (mirror API, don't wire):
  // scope carried by the parent Form (tenant) — per-field product/platform overrides deferred.
}

export type FormStatus = "draft" | "published" | "archived";

export interface Form {
  uid: string; // 'frm-…'
  code: string; // slug, unique per tenant (future)
  name: string;
  description?: string;
  status: FormStatus;
  entityType: "applicant" | "account"; // carried from platform-1; drives condition model picker
  fieldCount: number;
  createdAt: string;
  createdBy: string;
  // FUTURE: questionnaireDatasetCode?: string — the dataset this form feeds (§9). Not wired.
}

export interface FormResponse {
  // a filled-in form (standalone mode, §9)
  uid: string;
  formUid: string;
  values: Record<string, unknown>; // keyed by fieldKey
  submittedAt: string | null;
  submittedBy: string | null;
}
```

Design notes tying to the future API:

- `Form.status` reuses the `draft|published|archived` lifecycle the templates/datasets specs use — a form must be _published_ before it can back a questionnaire dataset.
- The eventual table is `documents.forms` + `documents.form_fields` (or a `crm.*` home if forms bind to CRM subjects) with the `0001` conventions: `id int` PK, `uid text UNIQUE`, `tenant_id int REFERENCES auth.tenants(id)`, RLS `tenant_id = ANY (auth.tenant_lineage())`, `created_by uuid`. **Do not build this now.** The mock omits `id`/`tenant_id` because the front end never sees them, exactly as the `Template` mock omits them.
- The platform-1 `productId`/`platformId` specificity axes and `resolveFields` merge are **not** modelled per-field in the mock; forms are firm-owned assets. The specificity merge is preserved as pure TS for the future backend pass but is not exercised by the UI.

---

## 4. Field-type catalogue + standard fields

**Offered field types (15 from platform-1, plus two repeatable types it never had).** The builder's type dropdown offers exactly the platform-1 set, in order: `text`, `textarea`, `number`, `checkbox`, `date`, `dateOfBirth`, `email`, `phoneNumber`, `bankAccountNumber`, `sortCode`, `currency`, `percentage`, `select`, `combobox`, `multiSelect` — followed by `beneficiaries` and, newest, `phoneNumbers` (§4.1), neither of which fits platform-1's flat single-value model. The renderer additionally tolerates legacy aliases present in platform-1 data but **not** offered by the builder — `multiPicker` (≡ `multiSelect`), `money` (≡ `currency`), `accountNumber` (≡ `bankAccountNumber`), `phone` (≡ `phoneNumber`) — so imported/legacy definitions still render (§6).

**Options-bearing types.** `requiresOptions = {combobox, select, multiSelect, multiPicker}`; `showsOptions = requiresOptions ∪ {currency, money}` (currency shows options only to carry `{currency}`). The options editor renders only for `showsOptions` types (§5.3).

**Closed person lists.** `title`, `gender`, `marital` and `employment` are field types whose options are the shared vocabulary in `pkgs/ui/src/lib/forms/vocabularies.ts` (`docs/client-vocabularies.md`). A form does not author them, and they are not in `requiresOptions`. Gender renders as a button group; the other three render as a select. A value already stored outside the list stays selectable, so a synced title is not blanked.

Per-type summary (drives §6 renderer mapping):

| fieldType(s)                     | Stored value            | Control (portal)                             | Origin                                 |
| -------------------------------- | ----------------------- | -------------------------------------------- | -------------------------------------- |
| text, (default)                  | string                  | `Input`                                      | inline                                 |
| textarea                         | string                  | `Textarea` / `TextareaWithMinCount`          | `textarea-with-min-count.tsx`          |
| number                           | number \| ''            | `Input type=number` + bounds                 | inline                                 |
| checkbox                         | boolean                 | `Checkbox`                                   | inline                                 |
| select                           | string                  | native `<select>` or `Select.*`              | inline                                 |
| combobox                         | string                  | `Command`/`Popover` combobox                 | `combobox-input.tsx`                   |
| multiSelect, multiPicker         | string[]                | multi-select popover                         | `multi-select-input.tsx`               |
| date, dateOfBirth                | ISO `YYYY-MM-DD`        | masked date input                            | `masked/date-input.tsx`                |
| currency, money                  | plain numeric string    | masked money (symbol prefix)                 | `masked/money-input.tsx`               |
| percentage                       | numeric string 0–100    | masked percentage                            | `masked/percentage-input.tsx`          |
| phoneNumber, phone               | `+<digits>` E.164       | masked phone                                 | `masked/phone-number-input.tsx`        |
| email                            | string                  | email input                                  | `masked/email-input.tsx`               |
| bankAccountNumber, accountNumber | digit string ≤8         | masked ≤8                                    | `masked/bank-account-number-input.tsx` |
| sortCode                         | `12-34-56` / partial    | masked `## - ## - ##`                        | `masked/sort-code-input.tsx`           |
| phoneNumbers                     | `{label,number}[]`      | repeatable label+number rows                 | bespoke — no platform-1 origin (§4.1)  |
| title, marital, employment       | book slug or title word | `SelectMenu`, options from `vocabularies.ts` | closed person lists                    |
| gender                           | `male` \| `female`      | `Segmented`                                  | closed person lists                    |
| repeating                        | object[]                | one row of scalar columns                    | authored columns (§4.2)                |

**Standard fields library (ported).** Port `apps/platform/lib/schemas/standard-fields.ts` as `pkgs/ui/src/lib/forms/standard-fields.ts` — the 31-field, 6-category catalogue (Client, Dates, Adviser, Financial, Rates, Document), typed `string|number|integer`, with `key`/`label`/`example`/`description`. In the Fields builder it seeds a **field-key suggestion picker** (choose a standard key like `client.full_name` and prefill label/type) rather than typing a raw `fieldKey`. It also aligns a form's answer keys with the template variable names the standard-fields library already defines, which is what makes the §9 questionnaire→template connection clean. Keep the two helpers (`buildFixturesFromKeys`, `buildInputSchema`) as-is for the future dataset/schema derivation.

### 4.1 The two repeatable types

`beneficiaries` and `phoneNumbers` are bespoke Svelte components, not a generic array wrapper around an existing type. Their shape is fixed (a beneficiary is a name and a portion; a phone number is a label and a number), so the builder does not edit their columns. `phoneNumbers` answers `{ label: string; number: string }[]`, `label` one of `home`/`work`/`landline`/`other`. Row order is authored order and is never sorted, and add/remove acts on the array held at `values[field.fieldKey]` rather than on per-row keys — a per-row `fieldKey` (`phone_0`, `phone_1`, …) cannot survive a row being deleted from the middle without a later row's value landing under an earlier row's name. Both post the whole array as a single value. `requiresOptions`/`showsOptions` do not apply to either. A schedule whose columns the firm chooses — dependants, pensions, mortgages — is the `repeating` field (§4.2), not a third bespoke component and not a wrapper over these two.

### 4.2 Repeating groups

Field type `repeating`. The column list lives on the field's `options`, as a list of `{key, label, type}`. `type` is a scalar the renderer already has (`text`, `number`, `currency`, `date`, `select`, and the other scalars in `REPEATING_COLUMN_TYPES` — not `repeating` itself, not `beneficiaries` or `phoneNumbers`, not `address` or `nationalIdentifier`). A choice column (`select`, `segmented`, `combobox`, `multiSelect`) carries its own `options` in the same shapes a standalone choice field uses. `required` on a column defaults to true; `required: false` leaves that cell optional.

The answer is an array of objects, one per row, keyed by column `key` — the same shape `DynamicForm.collect()` already stores at `values[field.fieldKey]`. Rows are not new holdings tables. Add and remove act on that array; deleting a middle row does not renumber the others.

One renderer, `pkgs/ui/src/lib/forms/field-inputs/repeating-input.svelte`, is used by both portals through `DynamicField`. The builder edits the column list in the field sheet.

Server-side `missing_required` (`apps/api/app/flows/form_validation.py`) rejects a required group with no row, and a required column left blank on any row — including a row started on an optional group. An optional group with no rows is fine. `0` and `false` count as answers. Client-side `validateField` applies the same rules. Conditional groups stay exempt on the server, as every other conditional field does.

### 4.3 Scoring

`documents.forms.scoring` (jsonb, null when the form is not scored) is the author's configuration:

```json
{
  "fields": [{ "field_key": "q1", "weights": { "a": 1, "b": 5 } }],
  "bands": [
    {
      "key": "cautious",
      "label": "Cautious",
      "min": 2,
      "max": 4,
      "definition": "…"
    }
  ]
}
```

`weights` maps an option's stored value to points. Bands are inclusive (`min` ≤ total ≤ `max`); if two overlap, the earlier one in the list wins. The builder's scoring panel picks the questions, sets the weights, and names the bands. Capacity-for-loss questions can sit on the same form and simply not be listed in `fields`.

The total is computed in Python on submit (`app/flows/scoring.py`), never trusted from the posted answers. It is stored on `documents.form_responses.outputs` (jsonb, not null, default `{}`):

```json
{ "band": "cautious", "score": 3, "label": "Cautious", "definition": "…" }
```

`band`, `label` and `definition` are null when no band contains the total; `score` is still the total. A posted answer whose key is `band`, `score`, `label` or `definition` is overwritten with the computed value. Those keys are not added to the answers when the client did not post them — they live on `outputs`.

`fill_form.config.remember` may name an output key (`{"risk_band": "band"}`). Promotion reads outputs first, then answers, and still copies only the keys the mapping names. The rest of the fact-find does not go into case memory.

**The seeded questionnaire.** `advice_risk_profile` (`app/flows/advice_seed.py`, a step of the fact-find journey defined there — `docs/flows.md`) is the worked example. Its twelve five-point agreement items are scored 1–5 each, the weight running with the risk-seeking end of the statement, so a reversed item ("I prefer a safe and steady return") weights _Strongly agree_ 1 and _Strongly disagree_ 5. Totals run 12–60 and fall into five contiguous bands: Cautious 12–21, Cautious to Moderate 22–31, Moderate 32–41, Moderate to Adventurous 42–51, Adventurous 52–60, each carrying its definition. The three-way product-choice question and the seven capacity-for-loss yes/no questions (with their conditional notes) sit on the same form and are not listed in `fields`, so they inform the adviser without moving the score.

---

## 5. Builder UI

Route layout under `apps/platform`, mirroring `templates/` and `datasets/` (list / detail / edit split), each `+page.server.ts` a thin auth guard returning `{}`, all data from `$lib/demo`:

```
apps/platform/src/routes/forms/+page.svelte              # list
apps/platform/src/routes/forms/+page.server.ts           # guard: if (!locals.user) redirect(303,'/login'); return {}
apps/platform/src/routes/forms/[id]/+page.svelte         # detail (preview + fields summary)
apps/platform/src/routes/forms/[id]/+page.server.ts      # guard + 404 if !getForm(params.id)
apps/platform/src/routes/forms/[id]/edit/+page.svelte    # the builder
apps/platform/src/routes/forms/[id]/edit/+page.server.ts # guard + 404
```

Read the route id via `page.params.id` from `$app/state` (not `$app/stores`), `const id = $derived(page.params.id ?? '')`, matching the template pages. The detail/edit `.svelte` also render a defensive "not found" empty state.

Component organisation, mirroring `components/templates/` (one PascalCase orchestrator + a lowercase pure-TS util folder):

```
apps/platform/src/lib/components/forms/
  FormBuilder.svelte            # orchestrator: field list + reorder + open editor sheet
  field-editor-sheet.svelte     # the Sheet body (was FieldsForm)
  conditions-editor.svelte      # Builder + JSON tabs (was ConditionsEditor)
  validation-editor.svelte      # Builder + JSON tabs (was ValidationEditor)
  DynamicField.svelte           # renderer (§6)
  DynamicForm.svelte            # section-grouping renderer wrapper (§6)
  field-inputs/                 # the masked/typed Svelte inputs (§6)
  editor/                       # pure TS, unit-tested
    types.ts                    # FormField/Form/… (or re-export pkgs/ui/src/lib/forms/types)
    visibility.ts               # LIFTED from pkgs/fields/src/runtime/visibility.ts
    options.ts                  # LIFTED from pkgs/fields/src/runtime/options.ts
    format.ts                   # LIFTED from pkgs/fields/src/runtime/format.ts
    validate.ts                 # rules→error derivation (replaces RHF baseRules, §6)
    masks.ts                    # LIFTED from pkgs/components/src/forms/masked/utils.ts
    standard-fields.ts          # ported (§4) — or live in pkgs/ui/src/lib/forms
```

`@graphene/ui` primitives to use (all verified exported from `pkgs/ui/src/index.ts`): `Button`, `Input`, `Textarea`, `Label`, `Checkbox`, `Switch`, `Badge`, `StatusBadge`; namespaced `Sheet`, `Select`, `Dialog`, `Table`, `Tabs`, `DropdownMenu`, `Command`, `Popover`; `Card`/`CardHeader`/`CardTitle`/`CardContent`. Simple dropdowns (scope filter, type list) can use a plain native `<select class="border-input bg-background h-9 rounded-md border px-3 text-sm">` as the platform pages do; `Select.*` where a styled trigger is wanted.

### 5.1 The field list (was `FieldsTable`)

A header row — title "Fields" / subtitle "Manage this form's fields" on the left, an "Add field" button on the right that opens the editor Sheet with no field — over a list/table of the form's fields. Columns matching platform-1: **Label** (font-medium), **Section**, **Type** (`Badge variant=outline`), **Required** (`StatusBadge tone=success` "Yes" / `tone=neutral` "No"), and a per-row `DropdownMenu` (Edit → open sheet with the field; Delete → confirm then remove). Search box filters by label/key. **Reorder** by native drag (no dnd-kit): on drop, recompute `sortOrder = index` across the list and persist (§7). Use `@graphene/ui` `Table` or a simple card list — the platform datasets/documents pages show both patterns.

Create/edit both open the same `Sheet` (right slide-over, not a modal — matches platform-1 and the portal `create-template-dialog.svelte`): title "Add field" / "Edit field", body = `field-editor-sheet.svelte`, footer Save.

### 5.2 The field editor (was `FieldsForm`)

Svelte 5 runes replace react-hook-form. One `$state` object holds the draft field; `$derived` flags drive conditional sections:

```ts
let draft = $state<FormField>(initialField ?? blankField());
const requiresOptions = $derived(
  ["combobox", "select", "multiSelect", "multiPicker"].includes(
    draft.fieldType,
  ),
);
const showsOptions = $derived(
  requiresOptions || ["currency", "money"].includes(draft.fieldType),
);
```

Two `Card`s, matching platform-1's order:

- **Card 1 "Field information".** Label (`Input`), Key (`Input`, or the standard-field suggestion picker from §4), Entity (`Select`: Applicant/Account — carried at the _form_ level in the portal, so this may be a read-only echo of `Form.entityType` rather than per-field), Type (`Select`, the 15 types §4), Section (`Input`), Required (`Switch` "Field is required"), Description (`Textarea rows=3`). The platform-1 tenant/product/platform scope selectors are **dropped** — scope is the owning form's concern, not per-field, in this rebuild.
- **Card 2 "Configuration".** Conditions (`conditions-editor.svelte`, help: "Control when the field is visible"), Validation (`validation-editor.svelte`, help: "Input constraints; visibility belongs in Conditions"), and — only when `showsOptions` — the Options editor (§5.3).

Save validation replicates the platform-1 Zod intent as plain runes-side checks (no zodResolver): label/fieldKey/fieldType required; options required and well-formed for select-like types; refuse `conditional` living in `validation` when `conditions` is empty (migrate it across on save, as platform-1 does). Surface errors in a destructive banner at the top of the sheet.

### 5.3 Conditions, validation, options editing

- **`conditions-editor.svelte`** — a `Tabs` with **Builder** and **JSON**. Builder: a mode `Select` (none / single / group), a Model select (application/applicant/account, gated by `Form.entityType` as in platform-1) + Property select (curated dotted-key lists, with a `__custom__` free-text escape), an Operator select (the 8 operators), and a Value input (comma-split for `in`/`notIn`, JSON-parse-then-fallback otherwise, hidden for `isEmpty`/`isNotEmpty`). Group mode adds an AND/OR `Select` and add/remove condition rows. Emits the exact platform-1 shape: `{ conditional: SimpleCondition | { logic, conditions[] } }` (or `''`/`null` for none). JSON tab: a raw `Textarea`, bound both ways, re-syncing the Builder on external change.
- **`validation-editor.svelte`** — a `Tabs` with **Builder** and **JSON**. Builder always shows placeholder + valueType + errorMessage; for text/textarea shows minLength/maxLength (+ rows for textarea) + pattern/patternMessage; for number shows min/max. Emits a flat `FieldValidation` object (deleting empty keys, `''` when empty). JSON tab: raw `Textarea`.
- **Options** — a **plain raw-JSON `Textarea`** (no structured UI, matching platform-1), shown only for `showsOptions` types, placeholder `[{"value":"option1","label":"Option 1"}]`; for currency/money the help notes `{"currency":"USD"}`. Parsed on save into `FieldOptions`.

---

## 6. Dynamic renderer

Two Svelte components plus the lifted runtime and the typed inputs. No react-hook-form: the form's answers are a single runes store; visibility and validation are `$derived`/`$effect` over it.

**Form store (replaces RHF `control`/`useWatch`).**

```ts
// inside DynamicForm.svelte
let values = $state<Record<string, unknown>>(initialValues ?? {});
let errors = $state<Record<string, string>>({});
// applicationData is the read-only cross-entity context (optional, defaults {})
const combined = $derived({ ...(applicationData ?? {}), ...values }); // values override, per platform-1
```

**`DynamicField.svelte`** — props `{ field: FormField; applicationData?; dataSources? }`, plus `bind:value` into the parent `values[field.fieldKey]` and an error slot. Behaviour ported from `dynamic-field-renderer.tsx`:

- **Visibility gate:** `const visible = $derived(isFieldVisible(field, combined, applicationData))`; `{#if visible}` … `{/if}` — a hidden field renders nothing (platform-1 returns `null`).
- **Options once:** `const options = $derived(resolveOptions(field, dataSources ?? {}))`.
- **Label:** `{field.label}{field.required ? '' : ' (optional)'}`.
- **`fieldType` → control** (same switch as platform-1, verbatim mapping in §4 table). Inline types (`text`/default, `textarea` sans min/max, `number`, `checkbox`, `select`) render `@graphene/ui` primitives directly; the rest delegate to `field-inputs/*` Svelte components.
- **Optional select:** its menu starts with a blank (`—`), so an answer picked by mistake can be cleared. Choosing it stores `''`. A required select has no blank.

**Typed/masked inputs to rebuild** under `apps/platform/src/lib/components/forms/field-inputs/` (each a small Svelte component taking `value`, emitting on change; the string transforms come from the lifted `masks.ts`):

| Component                               | Display / mask                                        | Stored value                           | Origin (platform-1)                    |
| --------------------------------------- | ----------------------------------------------------- | -------------------------------------- | -------------------------------------- |
| `MoneyInput.svelte`                     | symbol prefix (`Intl` narrowSymbol), `formatCurrency` | plain numeric string (commas stripped) | `masked/money-input.tsx`               |
| `PercentageInput.svelte`                | `formatPercentage` (clamp 100, 2dp)                   | numeric string 0–100                   | `masked/percentage-input.tsx`          |
| `SortCodeInput.svelte`                  | `## - ## - ##`                                        | `12-34-56` when 6 digits, else partial | `masked/sort-code-input.tsx`           |
| `PhoneNumberInput.svelte`               | `+## #### …`, leading 0→+44                           | `+<digits>` (≤15)                      | `masked/phone-number-input.tsx`        |
| `BankAccountNumberInput.svelte`         | digits ≤8                                             | digit string                           | `masked/bank-account-number-input.tsx` |
| `DateInput.svelte` (date + dateOfBirth) | `DD / MM / YYYY` from an 8-digit draft                | ISO `YYYY-MM-DD` (or undefined)        | `masked/date-input.tsx`                |
| `EmailInput.svelte`                     | passthrough, no autocap/correct                       | raw string                             | `masked/email-input.tsx`               |
| `ComboboxInput.svelte`                  | `Command`/`Popover`                                   | single string                          | `combobox-input.tsx`                   |
| `MultiSelectInput.svelte`               | multi popover                                         | string[]                               | `multi-select-input.tsx`               |
| `TextareaWithMinCount.svelte`           | char counter                                          | string                                 | `masked/textarea-with-min-count.tsx`   |

`phoneNumbers` and `beneficiaries` are deliberately absent from that table: neither is a masked
scalar input, so neither has a row-per-value transform to port. `phoneNumbers` gets its own
`field-inputs/PhoneNumbersInput.svelte` — a `SelectMenu` (label) beside the masked tel input
`PhoneNumberInput.svelte` already provides (number) per row, plus add/remove — built the same way
`beneficiaries` was, and neither has a platform-1 origin file because platform-1 never modelled a
repeatable value at all (§4.1).

**`DateInput` was planned with internal state and SHIPPED WITHOUT IT** — the draft-plus-`isUserEditing`
design above describes platform-1, not this codebase. What shipped is a native `<input type="date">`
whose value is the ISO string we store, so there is no draft to resync and nothing to port. Do not
reintroduce one: a local buffer synced from the prop by an effect is the shape that drops characters
when the form re-renders mid-entry.

**EVERY CONTROL COMMITS ON `input`.** This is the rule, and `date` was the one control that broke it:
it committed on `change` alone, which on a native date input means "when the value is complete AND on
blur". Between typing a date and leaving the field the form did not hold it, so anything that
re-rendered the form in that window — a dropdown pick, a `computedFrom` field resolving, a
conditional section appearing — left the answer on screen and out of `collect()`. What an adviser saw
was a filled-in box that Save called empty, which reads as the form eating text. `change` is kept
alongside `input` for a browser that commits a picker selection without an `input` event.

`field-entry.dom.test.ts` is the guard, and it is a SWEEP rather than a case: it mounts the real
`DynamicForm`, types into every kind the builder offers, and reads back what the form actually holds.
A kind must be swept or excused by name, so a new control cannot be added without answering this
question. Nothing cheaper catches it — an SSR test renders the control correctly, a unit test of the
input asserts on the `onchange` it was handed, and `svelte-check` is happy; the defect lives only in
the gap between the DOM and the form's state.

**Validation without RHF.** Port the platform-1 `baseRules.validate` logic into `editor/validate.ts` as a pure `validateField(field, value): string | null`:

- If not required and value is empty (`undefined`/`null`/`''`/empty array) → valid.
- If required and empty → `` `${field.label} is required` ``.
- `validation.minLength`/`maxLength` (string length; message `errorMessage` or default).
- `validation.pattern` (only when length > 0; `new RegExp(pattern).test`; message `patternMessage || errorMessage || 'Invalid format'`).
- number type: coerce, `isFinite` check, `min`/`max` bounds.

`DynamicForm.svelte` runs `validateField` for each **visible** field on submit (and optionally on blur) into `errors`; submit is blocked while any error is non-null. `DynamicField` shows its own error beneath the control.

**WHICH FIELDS ARE MANDATORY HAS TO BE ANSWERABLE BEFORE SAVE, AND FINDABLE AFTER IT.** The marker
convention is that an OPTIONAL field is labelled and a required one is not — right for a fact-find,
where nearly everything is required and the alternative is a column of asterisks that stops being
read. It only works if it is applied without exception, and `checkbox` had no marker at all, so a
required tick and an optional one were indistinguishable on the one control where the convention's
"unmarked" reads as "not needed".

After a refused submit the form now moves to the first failing field — focus (what a screen reader
follows and where the typing has to go) plus `scrollIntoView`, which honours the page's own
`--form-scroll-offset`. The section rail marks each section's dot destructive and states its failure
count, and the foot-of-form count is a button that jumps rather than a sentence that reports. A
fact-find is precisely the form where "7 fields need attention" is true and useless: the missing
answer is commonly three sections above the button just pressed.

**Hidden-field cleanup (ported from `DynamicFieldsSection`).** Group fields by `section` (fallback "Additional information"), render visible+sorted (`sortOrder ?? 999`) inside `Card`s (`grid gap-4 md:grid-cols-2`). Track the visible-key set in a `$state<Set<string>>`; a guarded `$effect` diffs it against the previous set and, for keys that transitioned visible→hidden, deletes `values[key]` and `errors[key]`. Guard so the effect only acts on an actual set change (the platform-1 comment warns that doing it every render loops — the same applies to runes).

**Read-only display.** Optionally port `DynamicFieldsDisplay` as `DynamicFormDisplay.svelte` using the lifted `formatFieldValue` — cards or sections variant — for the form detail page's "answers" preview. Low priority; the builder + fill renderer are the core deliverable.

---

## 7. Mock data + persistence

No backend. Follow the template editor's model exactly: seed in `$lib/demo`, mutate module arrays to fake writes, and persist authored content to `localStorage`.

- **Seed + helpers in `apps/platform/src/lib/demo.ts`.** Add a `forms: Form[]` seed array (a couple of realistic forms, e.g. a `frm-risk-questionnaire` mapping to the existing disabled `ds-risk-questionnaire` dataset, code `risk_questionnaire_output`), a keyed `fieldsByForm: Record<string, FormField[]>` side-table (mirrors the 1:N form→fields relation), lookup helpers `getForm(uid)` / `getFormFields(uid)`, and mutation helpers `addForm(input): Form` (increments a module `formSeq`, builds `uid = frm-${code}-${seq}`, `status:'draft'`, pushes into `forms` + seeds `fieldsByForm[uid]`), `saveFields(uid, fields)`, `publishForm(uid)`. Re-export the shared `Form`/`FormField`/… types from `@graphene/ui` through `demo.ts` so pages import everything from `$lib/demo` — the same re-re-export pattern already in `demo.ts` for the template types.
- **`generate-form.ts` is the closest existing precedent.** `apps/platform/src/lib/generate-form.ts` already models a template's input form (`TemplateForm`/`FormSection`/`FormField` + `getTemplateForm`, `isFormComplete`) and its header says the definition "drives both the interactive form and — conceptually — an automated/API render." Treat it as the template for authoring the Forms mock; the new Fields feature is the _authorable, conditional, typed_ generalisation of it. _(Since removed: once the §9.1 link went real — templates persist `input_form_code` and the generate dialog + editor resolve the linked form's actual fields — the per-doc-type mock and its adapter were deleted.)_
- **localStorage for authored content.** The template editor persists its authored schema to `localStorage`; the Forms builder persists the field list (and draft form metadata) the same way, keyed by form `uid`, so a reload keeps edits without a backend. Read seed on first load, prefer localStorage when present.
- **`dataSources` for options.** Provide a small mock `dataSources` map (e.g. `nationalities`) in `demo.ts` so `resolveOptions` `{source}` refs render in the builder preview and the renderer.

The persistence layer is deliberately swappable: when the real API lands, `getForm`/`addForm`/`saveFields`/`publishForm` become `@graphene/api-client` calls and the pages don't change — the same swap the template/dataset mocks are designed for.

---

## 8. Nav placement (platform portal, staff-only)

Nav is defined in `apps/platform/src/routes/+layout.svelte` and passed to `SaaSShell`. Templates and Datasets are **not** in the primary sidebar — they sit in a separate `menuItems: NavItem[]` list ("template-engine management lives in the account popout, not the primary nav"). Forms is a sibling template-engine concept, so add it to the **same `menuItems` array**, next to Templates and Datasets:

```ts
// apps/platform/src/routes/+layout.svelte
import ListChecksIcon from "@lucide/svelte/icons/list-checks"; // or another lucide glyph
const menuItems: NavItem[] = [
  { label: "Templates", href: "/templates", icon: LayoutTemplateIcon },
  { label: "Datasets", href: "/datasets", icon: DatabaseIcon },
  { label: "Forms", href: "/forms", icon: ListChecksIcon }, // ← add
];
```

Staff-only is enforced by the platform portal itself (it is the staff portal) plus the per-page `locals.user` guard. If Forms should be permission-gated rather than open to all staff, wrap the `menuItems` entry and the page loads in `can(permissions, role, 'forms:read')` (write implies read; system/admin bypass) — the same `can(...)` gating the Firm nav group uses. A `forms:read`/`forms:write` permission pair would be declared in `apps/api/app/permissions.py` **when the backend lands** (§11); for this mock pass, leave Forms open to any authenticated staff user and note the future gate. Extend the breadcrumb `$derived.by` in the layout with a `/forms/[id]` branch for the form name. No client-portal nav entry — clients never see this.

---

## 9. Future connection to templates (out of scope for now)

Documented so the mock shapes align; **not built in this pass.**

The portal already has the socket. `DatasetSource = 'csv' | 'sql' | 'static' | 'questionnaire'` and `dataset_versions.questionnaire_uid` exist in the model (`pkgs/ui/src/lib/templates/types.ts`, and the document-generation DDL §17), described in the docs as _"modelled but not wired end to end."_ The seed dataset `ds-risk-questionnaire` (code `risk_questionnaire_output`, `sourceType:'questionnaire'`, `enabled:false`) carries the description _"Computed risk profile from the client risk questionnaire. Not yet connected to the questionnaire system."_ — that disabled dataset **is** the intended join point.

Two intended modes:

- **Form → questionnaire dataset → template render inputs.** A published Form's responses become a `questionnaire`-sourced dataset: `dataset_versions.questionnaire_uid` points at the form (its `uid`/`code`), and the dataset's resolved rows are the form's answers keyed by `fieldKey`. Because §4 aligns a form's answer keys with the standard-fields variable names, those rows drop straight into the template variable context. Wiring it means: flipping `ds-risk-questionnaire.enabled` true, giving the Form a `questionnaireDatasetCode`, and (backend) resolving the dataset from stored `FormResponse`s. The document-generation worker then resolves it like any other dataset (that spec's §6/§10). None of this is built now; the mock `Form` carries the commented `questionnaireDatasetCode?` field so the shape is ready.
- **Standalone form + its own responses store.** The same Form, filled in without feeding a template, persists a `FormResponse` (§3) to its own responses store. **Now real for request-driven fills:** when an adviser's `complete_form` request item is completed in the client portal, the answers are persisted to `documents.form_responses` (a jsonb `answers` blob keyed by `fieldKey` — the `DynamicForm.collect()` shape), linked to the `request_item` and carrying the item's `form_uid` (migration `0010_form_responses`; see `docs/document-management.md`). Standalone (non-request) fills still mock to an array in `demo.ts`. The renderer (§6) is identical; only the persistence target differs.

### 9.1 Authoring UX — attaching a form + datasets to a template

A template connects to **one input form** (the questions filled at generate time) and **many datasets** (render-time data). A questionnaire-backed dataset is just an entry in the datasets list, so the author never meets a third concept — a Form feeds a template either _as the input form_ (filled live) _or as a questionnaire dataset_ (previously-captured responses).

**Form picking (decided).** Forms are reusable firm assets, **not** doc-type-locked. Each `Form` gains an optional `documentType` **affinity** (suggestion only, never a lock); the template's form picker **defaults to matching forms first** with a _"show all forms"_ toggle, and a **seeded default set** ships per firm (Fact Find, Risk Questionnaire, Illustration Inputs, Suitability Inputs, Review), cloned from the model tenant so a new firm isn't choosing from an empty list. **One input form per template** (it may have many sections). Rejected: auto-by-doc-type (a firm can't keep two illustration variants) and a flat unfiltered list (no guidance).

**Where it lives.**

- The **create-template wizard** gains an **Inputs** step (single-select form, pre-filtered by the chosen doc type, plus _show all_ / _none_ / _create a new form ↗_) between Details and the existing Datasets step.
- The **template detail page** gains an **Inputs & data** area: the linked form (with _Change_ / _Preview_) beside the existing Datasets list (_link_ / _remove_). Datasets keep their current multi-select pattern unchanged.

**Shape (mock → future).** `Template` gains `inputFormCode?: string` (single) alongside the existing `linkedDatasets: string[]`; `Form` gains an optional `documentType` for the affinity sort. The generate flow already anticipates this — the interim `getTemplateForm(type)` becomes `getForm(template.inputFormCode)` with no renderer change (§ generate-flow alignment). Still frontend/mock; **no backend in this pass.**

The API surface (**now built** — see §11): `GET/POST /forms`, `GET/PATCH/DELETE /forms/{uid}`, `POST /forms/{uid}/publish`, `GET/PUT /forms/{uid}/fields`, and `POST /forms/{uid}/responses` — each tenant-scoped via `tenant_session` and gated by a `forms:*` permission, mirroring the document conventions. The questionnaire→dataset resolution lives with datasets, not here (still deferred).

---

## 10. Build order

Frontend + mock only. A workable sequence:

1. **Lift the runtime.** Copy `visibility.ts`, `options.ts`, `format.ts` and `masked/utils.ts` (masks) into `editor/`, adapt imports to the mock `FormField` type, and bring their platform-1 unit tests across as vitest. Add `validate.ts` (RHF-free rules).
2. **Mock data.** Add `Form`/`FormField`/… types to `pkgs/ui/src/lib/forms/types.ts`, re-export via `@graphene/ui`; add the `forms` seed, `fieldsByForm`, lookups, mutation helpers and localStorage persistence to `apps/platform/src/lib/demo.ts`; port `standard-fields.ts`.
3. **Renderer first** (proves the runtime end to end). Build `field-inputs/*`, `DynamicField.svelte`, `DynamicForm.svelte` with runes-based values/visibility/validation and the hidden-field cleanup effect. Exercise against a seeded form.
4. **Builder.** `FormBuilder.svelte` (list + reorder), `field-editor-sheet.svelte`, then `conditions-editor.svelte` and `validation-editor.svelte` (Builder + JSON tabs), plus the raw options textarea.
5. **Routes + nav.** `/forms` list/detail/edit pages with thin guards; add Forms to `menuItems`; extend the breadcrumb branch.
6. **Detail preview.** Optional `DynamicFormDisplay.svelte` (read-only) on the form detail page.

Do the renderer before the builder so the field data shapes are proven by something that consumes them, exactly as the document-generation spec builds the generate flow before the editor.

---

## 11. Out of scope / open questions

**Since landed** (migration `0011_forms` + the `/forms` router):

- **`documents.forms` + `documents.form_fields`** — the form record + its ordered field list, staff-only RLS by tenant lineage (`tenant_id = ANY (auth.tenant_lineage())`), `created_by uuid`, `UNIQUE(tenant_id, code)`. Home is `documents.*` (alongside the render pipeline forms feed) — resolving open question #2. `conditions`/`validation`/`options` are opaque `jsonb` (the §3 shapes); the runtime interprets them.
- **`forms:read` / `forms:write`** in `app/permissions.py` (write ⇒ read), synced to `auth.permissions` on startup and emitted to `@graphene/api-client/permissions`.
- **The `/forms` router** (`apps/api/app/routers/forms.py`, `tenant_session`, gated per-endpoint): `GET/POST /forms`, `GET/PATCH/DELETE /forms/{uid}`, `POST /forms/{uid}/publish`, `GET/PUT /forms/{uid}/fields` (PUT replaces the whole ordered list, re-numbering `sort_order`; a field this form already owns keeps its uid across a re-save), `POST /forms/{uid}/responses` (standalone submission), and `GET /forms/{uid}/responses` (submissions received for a form — both client request-driven fills and standalone, keyed by `form_uid`, tagged `via`; surfaced in a **Responses tab** on the form detail page so submitted answers aren't write-only). **Lifecycle transitions run only through dedicated endpoints** — `PATCH` edits metadata only (no `status` field), so it can't bypass the checks below. `publish` is a **validated transition**: the form must exist, not be archived, and carry at least one field (422 otherwise) — a client can't be asked to fill an empty form. `DELETE` is a **soft archive** (`status='archived'`, hidden from `list` by default, fields/responses preserved, `uid` still resolvable) with a **reference guard** — it refuses (409) while any open (`pending`) `request_items.form_uid` still points at the form, so archiving can't strand a client mid-fill; and because `PATCH` can't set `status`, that guard has no side door. **Archive is reversible:** `GET /forms?include_archived=true` surfaces archived forms (the platform list has an **Archived** view) and `POST /forms/{uid}/unarchive` restores one — it comes back as a **draft** (re-earning publication via the validated transition, not silently republished), any `forms:write` staffer (advisers included; no admin dashboard). RLS + endpoint tests: `tests/test_forms_rls.py`, `tests/test_forms_api.py`.
- **The platform builder is wired to it** — `apps/platform/src/routes/forms/*` load from the API (server loaders) and mutate via form actions (`$lib/forms.ts` is the snake_case↔camelCase mapper); the builder autosaves the field list (debounced `PUT`). The `demo.ts` forms mock helpers remain only for the template form-picker.
- **The client fill flow is real** — `GET /documents/requests/{req}/items/{item}/form` (`get-request-form`, elevated `firm_session`, ownership-checked like the form-response submit) serves the client the builder form + fields for a `complete_form` item of their own request (clients have empty tenant lineage, so plain RLS can't reach a tenant-owned form). The client loader resolves each `complete_form` item's definition and the fill dialog renders it, then persists via the existing `POST …/form-response` (no more mock resolution or session-only "done"). Tests: `tests/test_request_form_api.py`. `dataSources` (reference data like nationalities) still comes from the shared bundle — it isn't tenant form state.

**Since landed earlier** (migration `0010_form_responses`): the `documents.form_responses` table + RLS, and the request-driven client-portal self-fill — a `complete_form` request item is completed in-app and its answers persisted (gated by the `documents:*` permissions + the fulfil ownership check). `0008` relaxes `request_item_id` to nullable and adds a `form_id` link so standalone responses share the table.

The **request dialog** (`request-document-dialog.svelte`, the adviser asking a client to complete a form) now offers the **real published forms** (threaded from `GET /forms` through the two platform documents pages), so the `form_uid` it stores on a `complete_form` item is a real `documents.forms` uid the client renderer can resolve — not a mock uid that would 404.

**Since landed:** the **template form-picker is real** — templates persist `input_form_code` (a real `documents.forms` code; `0012_templates`, see docs/document-generation.md), the detail page's picker lists the firm's real forms, the generate dialog resolves the linked form's actual fields, and the GrapesJS editor's `{{ token }}` autocomplete/chip picker draws its variables from those same fields (threaded to `TemplateEditor` via the edit page's `formFields`). The old per-doc-type mock input forms and the `generate-form.ts` adapter are deleted.

**Still deferred:** an **FK from `request_items.form_uid` to `documents.forms`** (kept as plain text so a request may reference a form uid before the row exists, and legacy/mock uids still resolve — the archive reference-guard mitigates the strand risk); the per-field `product`/`platform` specificity axes and `resolveFields` merge; and the questionnaire-dataset wiring (§9).

**Open questions:**

1. **Form vs flat registry.** platform-1 had a single global field registry keyed by `entityType`; the portal introduces a `Form` grouping. Confirm forms are the unit (they must be, to back a questionnaire dataset) and whether `entityType` stays per-form or is dropped once forms are firm assets.
2. **Home schema.** Do forms live in `documents.*` (alongside templates/datasets, since they feed generation) or in `crm.*`/a new `forms` schema (since they collect subject data)? Decides the future migration's home and FK targets. Deferred to `0002`.
3. **Structured options editor.** platform-1 kept options as a raw JSON textarea. Worth a structured builder (add/remove `{value,label}` rows, a `{source}` picker) in the portal, or keep the raw textarea for parity first?
4. **Condition property lists.** platform-1's Model→Property picker uses hardcoded curated dotted-key lists tied to its onboarding data model. The portal has no such model yet (`0002`), so seed the picker from the standard-fields keys (§4) plus a `__custom__` escape, and revisit once CRM lands.
5. **Combobox / multi-select primitives.** `@graphene/ui` exposes `Command`/`Popover` but no pre-built Combobox/MultiSelect; confirm we compose them (as platform-1's `combobox-input.tsx`/`multi-select-input.tsx` compose shadcn) versus adding shared components to `pkgs/ui`.
6. **Validation timing.** Validate on submit only, or on blur too? platform-1 (RHF `mode` varies) validates on change/blur; the runes rebuild can pick either — decide the UX before building `DynamicForm`.
