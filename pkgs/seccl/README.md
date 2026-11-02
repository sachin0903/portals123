# @graphene/seccl — typed Seccl payloads

Pydantic models for Seccl's API payloads, generated from Seccl's published Postman
collection. Types only: the HTTP client, auth and retries live in `services/seccl`
(`seccl_sync.client`).

## What this is generated from, and what that means

Seccl documents its API as a **Postman collection, not an OpenAPI spec**. There is no schema
anywhere in the 8.2 MB file — `spec/seccl.postman_collection.json` holds 377 requests with
205 example request bodies and 204 saved 2xx responses, and nothing else. So:

- **These types are inferred from examples.** They describe what Seccl's documentation
  shows, which is not the same as what Seccl's API guarantees.
- **`services/seccl` stays authoritative.** Its hand-written models were re-analysed against
  the live API (`seccl_sync.records`); where the two disagree, believe that one.
- **A field being absent here means no example carried it**, not that Seccl rejects it.

What the models _are_ good for: the write path. 205 documented request bodies mean the
create/update shapes are the best-evidenced part of the collection, and a rejected POST to
`/portfoliotransactiongroup` costs a payment rather than a `None`.

## Layout

```
spec/seccl.postman_collection.json   vendored byte-exact — provenance, never hand-edited
scripts/build_schemas.py             examples -> one JSON Schema per resource (the merge)
scripts/generate_models.py           schemas -> pydantic v2 modules (datamodel-codegen)
src/seccl/schemas/*.json             generated, checked in, reviewable
src/seccl/models/*.py                generated, checked in
```

13 resources are modelled — the ones carrying request bodies:
`portfoliotransactiongroup`, `client`, `account`, `clientproduct`, `portfoliotransaction`,
`portfoliotransactionaction`, `portfoliogroup`, `illustration`, `asset`, `model`, `webhook`,
`event`, `files`. The read-only rest are left to `services/seccl`, which already pulls them.

Each resource gets up to three shapes: `Account` (as Seccl returns it), `AccountCreate`
(POST body), `AccountUpdate` (PATCH/PUT body — partial, with Seccl's `updateId`
concurrency token).

## Usage

```python
from seccl.models import Account, PortfolioTransactionGroupCreate

account = Account.model_validate(response["data"])   # the `data` envelope is transport
print(account.wrapper_detail.wrapper_type)           # snake_case in Python

body = PortfolioTransactionGroupCreate(firm_id="SDEMO", ...)
await client.post("/portfoliotransactiongroup", json=body.model_dump(by_alias=True, exclude_none=True))
```

Fields are snake_case with camelCase aliases, and `populate_by_name` is on, so a model can be
built either way. **Dump with `by_alias=True`** — that is what Seccl reads. Nested types
(`BankDetails`, `WrapperDetail`, …) are imported from their resource module, not the package
root: different resources give the same name to different objects.

## Regenerating

```
moon run seccl:build-schemas      # collection -> src/seccl/schemas (review the diff)
moon run seccl:generate-models    # schemas -> src/seccl/models
moon run seccl:test               # fails if either output is stale
```

Both outputs are checked in and reviewed like hand-written code. `seccl:test` regenerates
into a temp directory and compares, so a hand-edit or a forgotten regeneration fails the
gate rather than drifting quietly.

To take a newer collection from Seccl: replace `spec/seccl.postman_collection.json` with the
fresh export (byte-exact — do not tidy it), then run both tasks and read the schema diff.
That diff is the API changelog Seccl doesn't publish.

## The merge, and why there is one

Feeding the examples straight to datamodel-code-generator is unusable: it infers a class per
occurrence, so `/account` alone produced `WrapperDetail`, `WrapperDetail1` and
`WrapperDetail2` for one concept, and marked fields required because the single example it
saw had them. `build_schemas.py` merges first — every example of a resource in one pass,
unioning keys and types all the way down, pooling each nested concept across all three
shapes. 136 classes across 13 modules, none of them numbered.

The judgement calls, each pinned by a test in `tests/test_build_schemas.py`:

| Decision       | Rule                                                                                               | Why                                                                                                                                                                                                                                       |
| -------------- | -------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Required       | present in **every** example of that shape                                                         | An absent key in one example is not evidence a field is optional; present-in-all is decent evidence it is not.                                                                                                                            |
| Enums          | never — observed values go in the field description                                                | The corpus is a handful of examples per field. A closed enum rejects valid production values.                                                                                                                                             |
| Dates          | `format: date` only for `^\d{4}-\d{2}-\d{2}$`; `date-time` only when every value carries an offset | `date.fromisoformat` accepts a full timestamp since 3.11 and silently drops the time — that typed `paymentDate` as a date and rejected what Seccl sends. Mixed naive/aware stays a string, because `date-time` generates `AwareDatetime`. |
| Numbers        | `number` unless the name is count-shaped and every value is integral                               | Money must not become `int` because the examples used round figures.                                                                                                                                                                      |
| Placeholders   | `{{firmId}}` counts as a string but is excluded from value sets and date detection                 | It is a hole the caller fills, not data.                                                                                                                                                                                                  |
| Nested objects | pooled by name across shapes, hoisted into `$defs`                                                 | One concept, one class — and pooling widens optionality, which is the safe direction.                                                                                                                                                     |

The gate that matters is `test_seccls_own_examples_validate`: all 488 documented payloads
parse against the models built from them, with errors at placeholder holes forgiven. That is
the only evidence inferred types can offer.
