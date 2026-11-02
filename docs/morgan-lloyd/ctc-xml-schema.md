# CTC Bespoke XML v1.6 (CTC1) — schema, section by section

Transcribed from `XML_v1.6_Bespoke.pdf` (CTC Software, 22/02/2021), which is the authority.
**M** = mandatory, **O** = optional, as the vendor marks them. Where our implementation disagrees
with this document, the document wins and the disagreement is recorded in §10.

Sections `adviser_fees`, `annuity_basis` and `results` (pages 21–29) are not transcribed _here_ —
they were read straight off the PDF into
[`illustration-fields.html`](./illustration-fields.html) (8 Aug 2026), which sets every element of
those three groups beside what we actually send.

## 1. Envelope

```
<message>
  <header>            message_version=CTC1, created_date, mode=Request
  <content>
    <quotations>      one only (non-repeating)
      <investor>      REPEATING — one per life
      <quotation>     REPEATING — one per product
        <plan_stream>       repeating
        <premium_split>     repeating
        <withdrawals>       repeating
        <crystallisation>   repeating
        <product_fees>      once per product
        <adviser_fees>
        <annuity_basis>
    <results>         response only
```

## 2. `header`

| Node              | M/O | Values                |
| ----------------- | --- | --------------------- |
| `message_version` | M   | `CTC1`                |
| `created_date`    | M   | `yyyy-MM-dd HH:mm:ss` |
| `mode`            | M   | `Request`             |

The response header carries `response_date` and `response` (`SUCCESS` / `ERROR`), and on failure
an `<errors><error>DESCRIPTION</error></errors>` block. Documented descriptions:

| Description                                             | Meaning                         |
| ------------------------------------------------------- | ------------------------------- |
| Unknown Product Code                                    | Invalid `product_ref`           |
| Error reading XML message                               | Malformed XML                   |
| An error has occurred when trying to produce the output | System calculation/output error |
| Specified fund not recognised. `<ref>`                  | `fund_ref` not recognised       |

**CTC does not honour this contract in practice.** A rejected request comes back as an HTML page
carrying a JavaScript `alert()`, with HTTP 200 — not the documented error envelope. Our client
parses the alert text out of the HTML for exactly this reason, and guards on the root element so
an XHTML error page cannot pass as success.

## 3. `quotations` — non-repeating

| Node               | M/O   | Notes                                                            |
| ------------------ | ----- | ---------------------------------------------------------------- |
| `effective_date`   | **M** | `yyyy-mm-dd`                                                     |
| `output_required`  | **M** | `Yes` / `No`                                                     |
| `output_reference` | O     | Names the generated PDF. Max 45 chars                            |
| `agent_name`       | O     | Max 130 chars. **Made optional 18/08/2020**                      |
| `client_type`      | O     | `Company` / `Trust` / `Individual`                               |
| `name`             | O     | Company/trust name — only when `client_type` is Company or Trust |

## 4. `investor` — REPEATING

Every field except the group itself is optional, including date of birth.

| Node             | M/O   | Values                                                                                        |
| ---------------- | ----- | --------------------------------------------------------------------------------------------- |
| `investor`       | **M** | At least one                                                                                  |
| `title`          | O     |                                                                                               |
| `forename`       | O     | Max 100                                                                                       |
| `surname`        | O     | Max 50                                                                                        |
| `sex`            | O     | `Male` / `Female`                                                                             |
| `life_type`      | O     | `Applicant` / `Life Assured` / `Both` / `Dependant` / `None`                                  |
| `marital_status` | O     | `Civil Partnership` / `Divorced` / `Married` / `Partnered` / `Single` / `Widowed` / `Unknown` |
| `date_of_birth`  | O     | `yyyy-mm-dd`                                                                                  |
| `exit_age`       | O     | Planned retirement/conversion age                                                             |

Repeating means **spouse and multi-life illustrations are supported** — the spec's own examples
show a `Dependant` spouse and a two-life investment bond. We only ever send one investor.

`exit_age` being optional is why the engine cannot catch an `exit_age` below the investor's actual
age: it has no obligation to receive either field. Validating that pair is unavoidably ours.

## 5. `quotation` — REPEATING, one per product

| Node                             | M/O   | Values                                                                     |
| -------------------------------- | ----- | -------------------------------------------------------------------------- |
| `product_ref` (attr)             | **M** | e.g. `SIPPEN`, `ISACC`, `QUALI`                                            |
| `product_type` (attr)            | **M** | e.g. `Self Invested Personal Pension`                                      |
| `quotation_type`                 | **M** | `Accumulation` / `Drawdown` / `Flexi Access Drawdown` / `SMPI` — see below |
| `inflation_adjustment_indicator` | O     | `Yes` / `No`                                                               |
| `current_max_income`             | O     | GAD maximum, capped drawdown only                                          |
| `next_review_date`               | O     | Next 3-yearly review, capped drawdown only                                 |
| `inc_already_taken`              | O     | Income already taken this review year, capped drawdown only                |

**The table is incomplete, and `Flexi Access Drawdown` is the fourth value.** The section's own
Example 1 uses it for a Self Invested Personal Pension — our exact product — and it is settled, not
an open question: [`data-mapping.md`](./data-mapping.md) records the element's set as all four, and
`test_ctc_xml_builder.py::TestQuotationShape` pins that the builder emits it.

**We send `Accumulation` and `Flexi Access Drawdown`** — the two the form offers
(`illustrations/form.ts`). Plain `Drawdown` is dropped because it is capped drawdown, closed to new
members since 2015, and `SMPI` because it is a statutory annual statement rather than something an
adviser quotes. (An earlier version of this note claimed we sent `Accumulation` and `Drawdown`; that
was never true of the form.)

`quotation_type` is the one closed vocabulary `xml_builder` does **not** put through `_one_of` —
worth pinning, since CTC answers a bad value with HTTP 200 and an HTML page rather than an error.

## 6. `plan_stream` — repeating, one per payment type

| Node                                                                | M/O   | Values                                                                     |
| ------------------------------------------------------------------- | ----- | -------------------------------------------------------------------------- |
| `plan_stream_key` (attr)                                            | **M** | Unique within the request                                                  |
| `contribution_type`                                                 | **M** | `Member` / `Employer` / `Transfer Value` / `Existing Fund Value`           |
| `net_gross`                                                         | **M** | `Net` / `Gross` — **when `contribution_type` is `Member`**                 |
| `amount`                                                            | **M** | Total contribution                                                         |
| `frequency`                                                         | **M** | `Weekly` / `Monthly` / `Quarterly` / `Half Yearly` / `Annually` / `Single` |
| `crystallised`                                                      | **M** | `Yes` / `No`                                                               |
| `plan_stream_start` → `start_type` (`Specified Date`), `start_date` | O     |                                                                            |
| `plan_stream_end` → `end_type` (`Specified Date`), `end_date`       | O     |                                                                            |

Two hard constraints:

- `Transfer Value` and `Existing Fund Value` **must** be `Single`.
- Only those two types may be `crystallised = Yes`; everything else must be `No`.

## 7. `premium_split` — **optional**, repeating

| Node                                           | M/O            | Notes                                                                   |
| ---------------------------------------------- | -------------- | ----------------------------------------------------------------------- |
| `premium_split`                                | O              |                                                                         |
| `stream_applicable` + `plan_stream_key` (attr) | **M**          | Repeat per stream this split covers                                     |
| `fund_split`                                   | **M**          | `type` attr optional — `COMMPROP` for commercial property               |
| `fund_ref`                                     | **M**          | Fund reference code                                                     |
| `fund_name`                                    | **M**          |                                                                         |
| `split_per` **or** `split_amount`              | **M — one of** | Use one style only across the whole `premium_split`                     |
| `rate_of_return`                               | O              | **The _mid_ growth rate for the fund**                                  |
| `management_charge`                            | O              | % management charge                                                     |
| `initial_charge`                               | O              | % initial charge                                                        |
| `generic_fund`                                 | O              | Must be `Yes` if `rate_of_return` and/or `management_charge` is present |

Note `rate_of_return` is documented as the **mid** rate, not the only rate — the engine derives
the lower and higher rates from it. That refines the observation in
`illustrations-report.md` §4: it is not that the field is ignored, it is that on the accumulation
runs the printed rates were the COBS standard 2/5/8%. Whether a supplied mid rate displaces the
standard middle rate needs the calibration run in §11 to settle.

## 8. `withdrawals` — optional, repeating

| Node                    | M/O   | Values                                                                                    |
| ----------------------- | ----- | ----------------------------------------------------------------------------------------- |
| `basis`                 | O     | `Fixed` / `Maximum GAD` / `Percentage of Maximum GAD`                                     |
| `amount`                | O     | Per frequency. Only when `basis = Fixed`, or a non-pension product                        |
| `percent`               | O     | Only when `basis = Percentage of Maximum GAD`                                             |
| `frequency`             | O     | `Single` / `Monthly` / `Quarterly` / `Half Yearly` / `Annually` / `2 Months` / `4 Months` |
| `start_date`            | O     | `yyyy-mm-dd`                                                                              |
| `increases` → `percent` | O     | Rate the withdrawal escalates at                                                          |
| `drawdown_type`         | **M** | `Flexible` / `Capped` / `UFPLS`                                                           |

## 9. `crystallisation` — optional, repeating (a separate group from `withdrawals`)

**This is how tax-free cash is instructed, and we never send it.**

| Node                                                                        | M/O   | Values                                                        |
| --------------------------------------------------------------------------- | ----- | ------------------------------------------------------------- |
| `drawdown_type`                                                             | **M** | `Flexible` / `Capped`                                         |
| `amount`                                                                    | O     | Amount being crystallised                                     |
| `frequency`                                                                 | O     | `Single` / `Monthly` / `Quarterly` / `Half Yearly` / `Yearly` |
| `antytxfcsh` (+ `type` attr)                                                | O     | Drawdown only. `Maximum` / `Amount` / `Percentage`            |
| `antytxfcsh` → `amount`                                                     | O     | When type is `Amount`                                         |
| `antytxfcsh` → `percent`                                                    | O     | When type is `Percentage`                                     |
| `allocation_basis` → `allocation` → `fund_split` → `fund_ref` / `split_per` | O     | Fund-specific tax-free cash (`allocation_basis = Specified`)  |
| `phasedcash` → `amount`                                                     | O     | Tax-free cash taken as income                                 |
| `phased_planning_option`                                                    | O     | `Non taxable income`, used with `phasedcash`                  |

## 10. `product_fees` — optional, but all-or-nothing

> "Where the section is present in the message it must **appear for each product** and contain
> **all the charges** applicable (ie the standard charges in the system standing data will be
> **ignored entirely**)."

This answers the omission question directly for this section: **omitting `product_fees` is not
"charge nothing"** — it falls back to the provider's standing data. Supplying it **replaces** that
data wholesale, so a partial list silently drops every charge you left out. Tiered charges must be
repeated for each product in the quotation with **identical** data.

| Node                                                      | M/O                                   | Values                                                         |
| --------------------------------------------------------- | ------------------------------------- | -------------------------------------------------------------- |
| `product_fee`                                             | O                                     | Repeat per non-tiered fee                                      |
| `fee_name`                                                | **M**                                 | Appears in the output. Max 50 chars                            |
| `fee_basis`                                               | **M**                                 | `Single` / `Ongoing`                                           |
| `fee_frequency`                                           | **M**                                 | `Single` / `Monthly` / `Quarterly` / `Half Yearly` / `Yearly`  |
| `fee_timing`                                              | **M**                                 | `In Advance` / `In Arrears` / `Before Growth` / `After Growth` |
| `fee_value_rate`                                          | **M**                                 | `Per Frequency` / `Per Annum`                                  |
| `fee_increases` → `increase_type` (`Fixed`), `percentage` | O                                     | Escalation                                                     |
| `fee_constraints` → `maximum_value` / `minimum_value`     | O                                     | Caps and collars                                               |
| `fee_tier_selection`                                      | O (**M if tiered**)                   | Always `Each Threshold`                                        |
| `fee_tiers` (+ `type` attr)                               | O                                     | `Percentage` / `Amount`                                        |
| `tier` (+ `upperval` attr)                                | O                                     | Use `999999999` for the top, unbounded tier                    |
| `fee_fund_basis`                                          | O                                     | `Pro-Rata` / `Client Select`                                   |
| `fund_split` → `fund_ref`                                 | M if `fee_fund_basis = Client Select` |                                                                |

`fee_tier_selection = Each Threshold` confirms **marginal banding**: each tier's rate applies only
to the slice of the balance within it, not to the whole balance.

## 11. Where our implementation diverges

Found by reading this spec against `illustration_matrix.py` and `apps/api/app/integrations/ctc/`:

| #   | Divergence                                                                                                                                                                                                                                                                     | Consequence                                                                                                                                                                              |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | The matrix sends `contribution_type = "Gross Contribution"` (`regular_contributions`, `young_long_horizon`, `comprehensive`). **Not a documented value** — the four are `Member`, `Employer`, `Transfer Value`, `Existing Fund Value`.                                         | Those runs' regular-contribution streams were probably ignored, which fits `regular_contributions` projecting only £7,640–£31,400 off a £25,000 pot. Must become `Member` + `net_gross`. |
| 2   | We never send `net_gross`, which is **M** for `Member` contributions.                                                                                                                                                                                                          | A correctly-typed member contribution would be incomplete.                                                                                                                               |
| 3   | We never send `<crystallisation>` — TFC was inferred from `crystallised="Yes"` on a stream.                                                                                                                                                                                    | Explains why `drawdown_flexible` produced no tax-free-lump-sum figure.                                                                                                                   |
| 4   | We send one `investor`; the group repeats.                                                                                                                                                                                                                                     | No spouse/dependant or multi-life illustrations.                                                                                                                                         |
| 5   | We only send `client_type = Individual`.                                                                                                                                                                                                                                       | No Company or Trust illustrations — relevant for SSAS.                                                                                                                                   |
| 6   | ~~`annuity_basis` is mandatory in our validator.~~ **Resolved.** `Quotation.pension` stopped enforcing it; the group is now supplied by `illustrations.house_annuity_basis()` for any caller that offers none, and omitted only on an explicit `include_annuity_basis: false`. | Not mandatory here, and CTC's own Drawdown example omits it — but omitting it zeroes the income column on an _accumulation_ quote (§12), which is why the default sends it.              |
| 7   | We never send `current_max_income` / `next_review_date` / `inc_already_taken`.                                                                                                                                                                                                 | Capped drawdown is unsupported.                                                                                                                                                          |
| 8   | `product_fees` is sent as a partial list.                                                                                                                                                                                                                                      | Per §10 it replaces standing data entirely, so unlisted charges vanish. Under `nodb=true` there may be no standing data at all — needs confirming.                                       |

Item 1 is a defect in the test matrix, not in the API's builder — but it means three of the
fourteen scenarios did not test what their names claim, and the report's figures for those rows
should not be relied on until they are re-run.

## 12. What omitting a section actually does

Measured with `illustration_matrix.py --omit comprehensive`, which drops one optional section at
a time from the fullest scenario and diffs the resulting PDFs.

| Variant            | Pages | First-year charge | Effect                                                  |
| ------------------ | ----- | ----------------- | ------------------------------------------------------- |
| baseline           | 14    | £6,909.37         | —                                                       |
| `no_product_fees`  | 13    | £6,047.79         | Charges fall by exactly the product-fee contribution    |
| `no_adviser_fees`  | 13    | £861.58           | Charges fall by exactly the adviser-fee contribution    |
| `no_fees_at_all`   | 13    | **£0.00**         | No charges at all                                       |
| `no_withdrawals`   | 13    | _absent_          | **Loses the entire drawdown summary** — 10 figures gone |
| `no_annuity_basis` | 14    | £6,909.37         | **Identical document.** No effect whatsoever            |

Four findings:

1. **Under `nodb=true` there is no standing-data fallback.** §10 warns that omitting
   `product_fees` makes the engine use the provider's standard charges; with no database attached
   it charges **zero** instead. So in our mode omission means "no fees", not "default fees". If we
   ever move off `nodb=true` this reverses, and a partial fee list becomes dangerous.
2. **Charges are additive and reconcile exactly**: £861.58 (product) + £6,047.79 (adviser) =
   £6,909.37 (both). The charge model is decomposable, which means it can be identified
   fee-by-fee from a handful of runs rather than reverse-engineered as a whole.
3. **`withdrawals` is what makes it a drawdown document.** Omitting it loses `annual_withdrawn`,
   `exhaustion_age`, `pre_retirement_funds`, `term_length`, the year-5/10 values and the whole
   reduction-in-yield block — the layout reverts to the accumulation form. `quotation_type` alone
   does not decide the document; the presence of withdrawals does.
4. **`annuity_basis` changes nothing _on a Drawdown document_** — same pages, figures and charge.
   That is a real result but a narrow one: this matrix ran against `comprehensive`, a Drawdown
   scenario, and only the **accumulation** layout carries a "yearly income worth" column. Re-run
   on an accumulation scenario the block is decisive — the income column goes from `[0, 0, 0]` to
   `[£1,590, £2,750, £4,660]` with the fund columns unchanged
   (`illustrations-report.md` §10). So: not mandatory, safe to relax in our validator, but
   **required whenever an annuity income figure is wanted**.

## 13. Not yet done

- ~~**Transcribe pages 21–29**: `adviser_fees`, `annuity_basis`, `results`.~~ **Done (8 Aug 2026)** —
  read straight off the PDF into [`illustration-fields.html`](./illustration-fields.html), which
  carries every element of those three groups with its M/O marking, alongside what we actually send.
  Three things those pages settled: `annuity_basis`'s six children are **all optional** (only the
  group is M, and only for a pension product); the spec's own two examples name the node
  `<annuity>` while its table says `<annuity_basis>` — we send the latter and the measured income
  column proves it is the one the engine reads; and the adviser-fee frequency list says **"Yearly"**
  where every other list in the document says "Annually".
- **Omission matrix**: drop each optional section in turn and diff the PDF text and page count,
  to establish empirically what disappears versus what falls back to standing data (§10 says
  fees fall back; the others are unknown).
- **Calibration runs**: the pairs described in `illustrations-report.md`, plus a specific test of
  whether a supplied `rate_of_return` displaces the standard mid rate (§7).
- **Fix items 1–3 above** in `illustration_matrix.py` and re-run the affected scenarios.
