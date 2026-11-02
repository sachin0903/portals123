# GIA and ISA illustrations

A GIA or ISA illustration is optional. It is not required to open the account, and it never gates onboarding. SIPP illustrations stay on CTC, and a SIPP still needs an unused CTC illustration before the account opens.

The adviser submits a request in the platform portal. A monthly engine computes the figures. The existing WeasyPrint worker prints a fixed PDF. The row is `platform.illustrations` on the client, the same table a SIPP quotation uses.

The engine does not call Seccl and it does not call SEI. It reads the model’s target return, the firm’s platform fee package, and the firm’s legal disclosure, and copies those onto the illustration. A Seccl firm and an SEI firm run the same loop.

## The two switches

Both are stored on the illustration so a later change can still regenerate an old document.

- `method` is `annex3`. The “before charges” column is a true no-charges run (COBS 13 Annex 3 Note 5). `seccl_addback` exists on the engine for a document that was built the other way; it is not the default.
- `growth_basis` is `real`. Balances are in today’s money. Inflation is a Graphene choice for these wrappers, and the document says so.

The template version marker (`gia-isa-3`) is part of the snapshot. The same layout is published into the firm’s document templates as **GIA and ISA illustration** the first time that library is opened, so it can be read and edited there. The next illustration prints the published version. Until the firm edits it, a newer code layout replaces the seeded version. The PDF opens on a cover in the firm’s colours, with the firm’s logo when one is stored, the time it was generated, and the low, medium and high growth rates beside the projected values. Tables are kept on a single page. An already stored PDF is left as it is until a new render job runs.

## What is stored

`platform.illustrations` gains `reference_snapshot` and `outputs`. Both are null on a CTC pension row.

- `inputs` is the adviser request, plus `product_type` (`GIA` or `ISA`).
- `reference_snapshot` is the target return, the three growth rates, the platform fee package, the legal text, the firm ink and accent colours used on the PDF, and the template version this run used.
- `outputs` is the engine result only: the three projected values, exhaustion months, first-year charges, the effect-of-charges table, reduction in yield, warnings, `method` and `growth_basis`.

The PDF is a `documents.documents` row, `document_type = 'illustration'`, queued for the worker. The document’s own `inputs` copy the request, the snapshot and the outputs, so a re-render does not read live rates. Rows are insert-only.

While the job is queued the listing shows **generating**. A failed render leaves the row and the error, and files no figure-less success.

## Where each input lives

Generation blocks, and writes nothing, when the model has no target return, the firm has no usable platform fee package, or the legal disclosure is incomplete. A discretionary manager charge that is not on the model is treated as nil. Fund costs are not applied.

| Input                                                                                                                                                    | Level                                                                              | Who maintains it                                                                                                                                     |
| -------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Wrapper, term, contributions, withdrawals, existing balance, other platform assets, adviser charges, optional DFM override, joint owner (GIA cover only) | This illustration                                                                  | The adviser, in the platform portal. The client’s age is not required.                                                                               |
| Growth                                                                                                                                                   | The model’s target return                                                          | Mid is the target less 2 percentage points of inflation. Low and high are 3 points either side. A model with no target return cannot be illustrated. |
| Platform fee                                                                                                                                             | The firm’s fee catalogue (`platform.fee_packages`, category containing “platform”) | The adviser picks one when the firm has more than one. Rates on the package are percentage points.                                                   |
| Legal entity, FCA number, company number, registered office, future-performance warning, and whether the firm issues these illustrations                 | The tenant (`auth.tenants`)                                                        | The firm. Graphene admin can enter them when the tenant is set up.                                                                                   |

Asset-class mix, discretionary manager charge, holdings and per-fund costs are not required. Morgan Lloyd illustrations do not carry them. The ISA allowance and custodian cash interest are not required either.

The firm flag `gia_isa_illustrations` defaults off. The new-illustration page always offers SIPP, GIA and ISA. With the flag off, choosing GIA or ISA explains that those illustrations are switched off for the firm, and the SIPP form is unchanged. With the flag on, a GIA or ISA needs a model with a target return, a platform fee package, and the firm’s legal disclosure. Where the firm has more than one platform fee package, the adviser picks one. The SIPP composer and the SIPP drawer are unchanged.

A rendered illustration — SIPP, GIA or ISA — is **not shown to the client until a member of staff shares it**. The document is filed under the client (`intended_contact_id`, so it lists on their record) but the `'subject'` link that is the client's read permission is withheld, both on the SIPP path (`routers/illustrations._store_pdf`) and in the worker's render path (`worker._persist`, through `documents/service.shared_on_render`). **Share** on the client's illustrations list (platform portal, and the admin operate list) asks for confirmation that names the client, then binds that link for one row. The firm flag `illustrations_auto_share` (`auth.tenants`, 0248; off by default) restores filing-on-render for a firm that wants every illustration in the portal at once. Both flags are set on the admin portal's per-tenant **Illustrations** page, alongside the legal disclosure.

## The monthly loop

One loop, five runs: low, mid and high with all charges, mid with adviser charges off, and mid with every charge off. Balance starts at nil. Each month: contributions (a regular amount escalates on the anniversary of its start), adviser initial on the first contribution, dealing charge on new money, withdrawals capped at the balance, then growth, then charges on the post-growth balance divided by 12. The platform tier is recomputed every month from the balance plus the other platform assets the adviser stated. If the balance hits nil, the month is recorded and charges and further withdrawals stop. Reduction in yield is omitted when the mid scenario exhausts.

Growth is the model’s target return less 2 percentage points. Low and high are 3 points either side of that. The nominal equivalent is checked against the COBS 13 Annex 2 cap (5 percent ISA, 4.5 percent GIA). Above the cap, the rate is capped and a warning is stored.

Rounding happens only at output: nearest pound in the projection tables, pence in the first-year charges, rates to two decimal places as a percent.
