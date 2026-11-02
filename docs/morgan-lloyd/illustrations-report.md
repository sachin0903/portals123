# Illustrations: what CTC computes, and what building our own would take

Evidence for this document was produced by `services/morgan-lloyd/scripts/illustration_matrix.py`
on 31 July 2026 against `camtest.ctc.uk.com`. Fourteen scenarios, from a one-line accumulation
quote to a full drawdown with tiered adviser fees. Re-run it with
`moon run morgan-lloyd-sync:illustration-matrix`; artefacts (request XML, response XML, PDF,
extracted text) land in `services/morgan-lloyd/.data/illustrations/matrix/`.

## 1. The single most important finding

**The engine rejects nothing.** All fourteen requests — including a nil-value pot and an
`exit_age` below the investor's actual age — returned HTTP 200, `<response>Success</response>`
and a real multi-page PDF. There is no validation layer to lean on. An illustration built from
bad input does not fail; it produces a confident, well-typeset, wrong document that goes to a
client.

Two of the fourteen produced provably wrong output:

| Scenario                   | What the PDF says                                                                                             | Why it is wrong                                                                                                                                                                     |
| -------------------------- | ------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `invalid_exit_age_in_past` | "Term length: 20 February 2024", "Chosen Final Annuity Age 20 February 2024"                                  | DOB 1959 with `exit_age` 65 ⇒ maturity **two years in the past**. Engine produced 12 pages regardless.                                                                              |
| `comprehensive`            | Fund worth £753,000 at year 5 and £888,000 at year 10, but "your drawdown fund will run out when you are: 62" | Age 58 now, so it claims a **growing** £888k pot is exhausted in four years. Internally contradictory, and it survived a correction to the input (below), so it is CTC's, not ours. |

The first is the fault that was already shipped: `GRAPHENE-TEST-0000.pdf`, generated before this
harness existed, carries the same past-dated maturity. Nothing in the response flags it, which
is why the check has to live on our side.

`exit_age` **must** be validated against date of birth before we call CTC. That is a hard
requirement, not a nicety.

## 2. Coverage

| Scenario                          | Pages | What it exercises                                  | Result                              |
| --------------------------------- | ----- | -------------------------------------------------- | ----------------------------------- |
| `minimal_accumulation`            | 7     | £100k transfer in, no fees, no withdrawals         | clean                               |
| `regular_contributions`           | 7     | £500/month (`Member`/`Gross`) alongside a lump sum | clean                               |
| `existing_fund_plus_transfer`     | 7     | Existing pot + incoming transfer                   | clean                               |
| `with_product_fees`               | 6     | Monthly admin, scheme return, PAYE setup           | clean                               |
| `with_tiered_adviser_fees`        | 7     | Four-band percentage charge, each-threshold        | clean                               |
| `drawdown_flexible`               | 12    | Crystallised pot, £750/month flexi-access          | clean                               |
| `drawdown_with_growth`            | 12    | Same with a positive real return                   | clean                               |
| `crystallised_and_uncrystallised` | 13    | Both pots plus a transfer, two withdrawal streams  | clean                               |
| `young_long_horizon`              | 7     | Age 35→65, a 30-year projection                    | clean                               |
| `near_retirement`                 | 6     | Age 64→75, short horizon                           | clean                               |
| `inflation_adjusted`              | 6     | Real rather than nominal terms                     | clean                               |
| `comprehensive`                   | 14    | Everything at once                                 | **contradiction**                   |
| `invalid_exit_age_in_past`        | 12    | Deliberately bad `exit_age`                        | **past maturity**                   |
| `zero_amount`                     | 6     | Nil pot                                            | clean — a document of honest zeroes |

`zero_amount` is worth calling out as a pass: the engine printed zeroes throughout rather than
inventing a projection. That is the correct behaviour for degenerate input.

## 3. There are two different documents

The layout depends on `quotation_type`, and anything reading these PDFs must handle both.

**Accumulation** (6–7 pages) gives a **low/mid/high triplet** against the regulator's gross
rates, never a single number:

```
When you reach age 65 your retirement fund
could then be worth in real terms:              £49,800   £76,000   £114,000
your retirement fund as a tax-free lump
sum. In real terms this could be worth:         £12,400   £19,000    £28,600
```

**Drawdown** (12–14 pages) gives single figures plus a Key Information Summary: tax-free lump
sum, amount remaining for drawdown, pre-retirement funds, total withdrawn annually, year-5 and
year-10 values, the age the fund runs out, and the reduction-in-yield block.

Matching only the drawdown labels made nine of fourteen scenarios extract **nothing** while
still reporting "clean". The harness now treats an empty extraction as a failure — an audit that
checked nothing must never read as a pass.

## 4. `rate_of_return` is ignored for accumulation

`minimal_accumulation` sent `<rate_of_return>0</rate_of_return>` and got projections of
£49,800 / £76,000 / £114,000 on £100,000 — plainly not zero growth. The PDF states its own
assumptions:

> "growth rates of 2%, 5% and 8% for each fund … the gap between the lower rate and the middle
> rate, and the middle rate and the higher rate is fixed at 3% … Inflation is assumed to be 2.0%
> each year."

So accumulation projections use **COBS 13 Annex 2 standard rates**, and our fund-level
`rate_of_return` is overridden. Real terms are 2/5/8 less 2% inflation, printed as
−3.0% / 0.0% / 3.0% nominal-of-real and −4.9% / −2.0% / 1.0% after charges.

Drawdown behaves differently: there `growth_rate` came back as **2.9%** and responded to input.

The practical consequence: **do not show a client a growth rate we chose** on an accumulation
illustration. The document will contradict us.

## 5. What CTC actually calculates

Everything below is computed inside the PDF. The XML response contains **no results at all** —
it echoes the request and appends one element:

```xml
<results><document_location>https://camtest.ctc.uk.com/PDFPortalArchive/<ref>.pdf</document_location></results>
```

That answers a question left open in `docs/contributions.md`: `document_location` **is** a URL,
so the endpoint can fetch and file the PDF.

| Output                            | Observed                                       | Notes                                                             |
| --------------------------------- | ---------------------------------------------- | ----------------------------------------------------------------- |
| Projected fund, 3 rates           | `[49,800, 76,000, 114,000]` on £100k           | COBS rates, real terms, after charges                             |
| Projected fund with contributions | `[69,400, 102,000, 156,000]` on £25k + £500/mo | Regular contributions do compound in                              |
| Tax-free cash, 3 rates            | `[12,400, 19,000, 28,600]`                     | Exactly 25% of each fund column, in all 8 accumulation runs       |
| Yearly income, 3 rates            | `[0, 0, 0]` → `[1,590, 2,750, 4,660]`          | Zero without `annuity_basis`; **CTC supplies the rate** — see §10 |
| Total investment                  | £100,000                                       | Sums the single-premium streams                                   |
| Retirement age                    | 65                                             | Derived from DOB + `exit_age`                                     |
| Tax-free lump sum                 | £37,500 on £150k                               | Drawdown only; 25% of the crystallised pot                        |
| Remaining for drawdown            | £112,500                                       | Crystallised less PCLS                                            |
| Total withdrawn annually          | £9,000                                         | £750 × 12; £12,600 for £750+£300 monthly                          |
| Year 5 / year 10 value            | £95,100 / £49,200                              | Drawdown only                                                     |
| Fund exhaustion age               | 83                                             | **Unreliable — see §1**                                           |
| First-year charge                 | £0 → £135.29 → £6,909.37                       | Zero with no fees; rises with product then adviser fees           |
| Reduction in yield                | 2.9% gross − 0.8% = 2.1% net                   | Only populated once charges exist                                 |
| Fund OCF table                    | 0.00% for Cash                                 | Initial charge + ongoing charges figure per fund                  |

The charge figures are the cleanest signal that the engine is doing real work:
`crystallised_and_uncrystallised` (product fees only) gives a £135.29 first-year charge and a
0.0% yield reduction, while `comprehensive` (product fees **plus** a tiered adviser charge) gives
£6,909.37 and 0.8%. The tiered adviser fee is doing almost all of that.

## 6. What implementing our own would require

Feasible, and considerably more than a projection formula. In rough order of difficulty:

**Regulatory core — the part that must be exactly right**

1. **COBS 13 Annex 2 projection rates.** Standard 2/5/8% gross with a fixed 3% inter-rate gap,
   2% assumed inflation, real-terms conversion. These are prescribed, they change, and using
   stale rates makes every document non-compliant.
2. **Reduction in yield.** Gross rate less the annualised effect of all charges, expressed to one
   decimal. This is a solve, not a subtraction: charges are levied at different frequencies and
   timings (`Before Growth` vs `In Advance`) against a balance that is itself changing.
3. **PCLS arithmetic** — 25% of the crystallised pot, with the lump sum allowance cap.
4. **Annuity rates** for the "yearly income worth" column. **Cheaper than it looks** — the rate is
   prescribed, not purchased. See §10: CTC states in the PDF that "the cost of the income we've
   used is based on a price set by our regulator", so this is a COBS parameter to implement, not a
   commercial feed to buy. It still has to track escalation (RPI), frequency and timing
   (advance/arrears), and it adds to the rate-maintenance burden in item 1.

**Charge engine — where the complexity actually lives**

5. Per-fee `basis` (Ongoing/Single) × `frequency` (Monthly…Yearly/Single) × `timing`
   (Before Growth / In Advance) × `value_rate` (Per Annum / Per Frequency), each with its own
   `fee_start`/`fee_end` window and a VAT flag. That is a small rules engine per fee.
6. **Tiered percentage fees** with `fee_tier_selection = Each Threshold` — marginal banding, not
   a single rate on the whole balance. Our own £6,909 first-year figure came almost entirely from
   this, so it is the highest-value thing to get right and the easiest to get wrong.
7. Fund-level OCF and initial charges, per fund, with a premium split across streams.

**Projection mechanics**

8. Monthly-stepped cashflow: contributions in, withdrawals out, charges at their own cadence,
   growth applied per period — for accumulation, drawdown, and the crystallised/uncrystallised
   split simultaneously.
9. Fund-exhaustion search. Note CTC's own answer is unreliable (§1), so this is a place to be
   _better_ than the incumbent, not merely equal.
10. Decimal arithmetic throughout. We already hit `Decimal.normalize()` rendering 60 as `6E+1`
    in `xml_builder.py`; money and rates cannot be floats.

**Document production**

11. A 6–14 page FCA-compliant KFI with prescribed wording, per-firm branding, and the two
    distinct layouts. We already have the machinery — `app/worker.py` (Jinja2 → WeasyPrint) with
    `documents.templates` — so this is the least novel part.

**Assurance**

12. This harness, inverted: every scenario becomes a golden test against CTC's output, so our
    engine is proven equivalent before it replaces anything. That is the real reason to keep the
    matrix — it is the acceptance suite for a replacement.

**Recommendation.** Do not rebuild in order to save a call — but the case is less lopsided than
it first appeared. I previously said the annuity rate was an external dependency we would have to
buy; §10 shows that is **wrong for illustration purposes**, because the rate is regulator-set.
That removes the one hard external dependency.

What remains against rebuilding is the ongoing **compliance liability** of items 1, 2 and 4: the
projection rates, the annuity price and the reduction-in-yield basis are all FCA-maintained
parameters, and stale values make every document we issue non-compliant. That is an
organisational commitment, not an engineering estimate.

A rebuild is worth it to escape a hard constraint — per-firm branding CTC will not do, latency, or
CTC's own defects (§1). Meanwhile the cheap win is unchanged: **input validation on our side**,
which is a day's work and removes the whole class of confidently-wrong document.

## 10. CTC supplies the annuity rate; we supply only the shape

The omission matrix result for `annuity_basis` (`ctc-xml-schema.md` §12) was measured on a
**Drawdown** scenario — and the drawdown layout has no annuity income column, so "no effect" was
measured in the one place it could not have one. Re-run on an accumulation scenario, which does
carry the column, the effect is unambiguous:

|                                                           | Projected fund               | Yearly income                |
| --------------------------------------------------------- | ---------------------------- | ---------------------------- |
| `minimal_accumulation` (no `annuity_basis`)               | £49,800 / £76,000 / £114,000 | **£0 / £0 / £0**             |
| `accumulation_with_annuity` (identical + `annuity_basis`) | £49,800 / £76,000 / £114,000 | **£1,590 / £2,750 / £4,660** |

The fund columns are identical; only the income appears. We sent `annuity_frequency=Monthly`,
`annuity_timing=Advance`, `escalation=RPI` — no rate — and the PDF renders exactly that basis back:

> "The income is based on you buying: a monthly income payable in advance throughout your
> retirement, increasing in line with the Retail Price Index each year."

and names its own source:

> "The cost of the income we've used is based on **a price set by our regulator** for use in
> illustrations."

So the division of labour is: **we specify the annuity's shape, CTC applies the prescribed price.**
It is not quoting a market rate, and neither would we — for a KFI the rate is a rule.

The implied rates are not constant across the three columns:

| Fund     | Income | % of full fund | % of fund less 25% PCLS |
| -------- | ------ | -------------- | ----------------------- |
| £49,800  | £1,590 | 3.193%         | 4.251%                  |
| £76,000  | £2,750 | 3.618%         | 4.825%                  |
| £114,000 | £4,660 | 4.088%         | 5.457%                  |

Rising with the projection rate, which means the annuity interest assumption is tied to the
projection basis rather than being a single fixed factor. Anyone reimplementing this needs that
relationship, not one rate — and it is the next thing the calibration runs should pin down.

## 7. Checks the harness enforces

Each is an invariant that a plausible-looking wrong document would break:

1. Any printed maturity/annuity date must be in the future.
2. Tax-free cash ≤ 25% of the crystallised pot.
3. Tax-free cash + remaining drawdown reconstructs the crystallised pot.
4. Annual withdrawal total = amount × periods per year.
5. Charges bite when fees are sent, and are zero when they are not.
6. Reduction in yield reconciles: gross − reduction = net.
7. Fund exhaustion age is above the investor's current age.
8. A funded plan with no withdrawals cannot project to zero at year 5.
9. The investor's surname appears in the document.
10. The accumulation triplet ascends, and its tax-free cash column is a quarter of the fund
    column.
11. A fund growing between years 5 and 10 cannot also be "running out" imminently.
12. **An extraction that found no figures is a failure, not a pass.**
13. No `NaN`, `#REF`, or `Infinity` in the text.

## 8. Follow-ups

- ~~**Validate `exit_age` against DOB** before calling CTC, in `xml_builder.py`. Highest priority.~~
  **Done** — `Investor._check_exit_age` refuses a retirement age that is not in the future (the
  engine cannot catch it: §4 of the schema doc explains that both fields are optional to CTC), and
  one above `MAX_EXIT_AGE`. A drawdown quote for someone already past the recorded age is still
  produced: `projection_exit_age` sends age 99, because the recorded age (at most 75) would print
  a term that has already ended. Accumulation to a retirement already reached is still refused.
- **Measure whether `management_charge` on a `fund_split` moves anything.** As of 04 Aug 2026 the
  API sends `platform.models.model_charge` there (`routers/illustrations.py::_premium_split`); before
  that it sent nothing, so the model's own charge never reached the engine at all. It is untested
  against the live engine, and §4 is the reason not to assume: fund-level `rate_of_return` IS
  overridden on an accumulation quote, so a fund-level element being accepted is not evidence it is
  used. The charge figures did respond to product and adviser fees (§5), which makes it plausible.
  One run with and without, at the same scenario, settles it — and decides whether the drawer may
  tell an adviser the projection is net of the model charge. Until then it says "sent", not
  "applied". If it turns out to be ignored, the model charge belongs in `product_fees` instead.
- Ask CTC about the exhaustion age reported in `comprehensive` and
  `crystallised_and_uncrystallised` — both say 62 on a large, growing pot.
- `annuity_basis` is currently mandatory in our validator. The omission matrix
  (`ctc-xml-schema.md` §12) shows removing it produces a **byte-for-byte equivalent** document —
  same pages, same figures, same charge. Relax it.
- The charge model is **additive and reconciles exactly** (£861.58 product + £6,047.79 adviser =
  £6,909.37 both), so it can be identified fee-by-fee. That is the strongest evidence yet that
  reverse-engineering the calculations is tractable in a dozen runs.
- The `Total investment` line does not total the pot. It reported £185,000 for
  `existing_fund_plus_transfer` (which sent £245,000 across two streams) and, once regular
  contributions were being accepted, £6,000 for `regular_contributions` (= £500 x 12, the
  annualised regular stream, not the £25,000 lump sum). Its meaning shifts with the stream mix,
  so do not surface it as "amount invested" anywhere.

## 9. Correction: three scenarios were testing the wrong thing

Reading `XML_v1.6_Bespoke.pdf` after the first run showed this harness had been sending
`contribution_type = "Gross Contribution"`, which is **not one of the four values the spec
allows** (`Member`, `Employer`, `Transfer Value`, `Existing Fund Value`). The engine accepted it
without complaint and appears to have discarded the stream. Correcting it to `Member` with the
mandatory `net_gross` changed the answers by a factor of four to seven:

| Scenario                       | With the invalid type        | Corrected                          |
| ------------------------------ | ---------------------------- | ---------------------------------- |
| `regular_contributions`        | £7,640 / £15,600 / £31,400   | **£69,400 / £102,000 / £156,000**  |
| `young_long_horizon`           | £36,800 / £89,600 / £210,000 | **£234,000 / £416,000 / £772,000** |
| `comprehensive` (yr 5 / yr 10) | £693,000 / £766,000          | **£753,000 / £888,000**            |

Two lessons, both bigger than the bug:

1. **An undocumented enum value is silently dropped, not rejected.** This is the §1 finding again
   in a new place, and it is the strongest argument for validating our payloads against the
   spec's enumerations before sending. Nothing in the response or the PDF hinted that a whole
   contribution stream had been ignored — the document simply projected a smaller fund.
2. The `comprehensive` contradiction **survived** the correction, so it is a genuine engine or
   data fault rather than an artefact of our bad input. Worth raising with CTC on that basis.

Figures elsewhere in this report are from the corrected run except where a row is explicitly
labelled as the earlier one.
