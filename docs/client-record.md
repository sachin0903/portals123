# The client record

Five independently-editable cards (contact, bank, personal, pension, employer), a
Vulnerability card (its own endpoint — the pencil opens the drawer), plus a
Compliance card (ticks read-only; source of wealth a picker and supporting statement) and beneficiary nominations on the
account. Migrations
`0138_contact_employment` and `0139_client_sections`; the surface is
`pkgs/ui/src/lib/components/book/clients/client-record.svelte`, mounted by the platform portal at
`/clients/{uid}` (Overview) and `/clients/{uid}/details` (the person cards), and read-only by the
admin portal's book (which stacks both bands).

The identity header states **email, primary number and address** in a wrapping row (xs, icon each;
hover or focus reveals a copy icon, click copies and toasts), then
type · **status** (Active / Lead / Suspended / Closed) · **client book** (the
[classification](client-classifications.md) that picks their portal design — staff with
`contacts:write` can pin or rematch) · portal access. **No portal access**
opens an action menu (Invite to portal, when the caller may; Manage profiles). **Portal Access**
still opens the logins dialog. Closed replaced Archive; there is no Deactivate — Suspended is the
custodian's word (`provider_status`). Close is offered only when the client holds no accounts, or
every account is worth zero. Overview keeps a read-only copy of Personal details next to the
figures and accounts; Details has Contact / Bank / Personal / Pension / Employer /
Vulnerability (with the pencils). Bank details are one set per person — used by every account they hold when the account
has none of its own; the account record says so when it is inheriting, and the account card writes
`platform.accounts.bank_accounts` when it has its own. **Whether an account may hold bank details
at all is a product-shelf flag** (`platform.products.bank_details_allowed`, set in admin at
`/tenants/{uid}/platform/products` — “Allow withdrawal bank details”). JISAs default off because
money cannot be withdrawn. The account record hides the pencil and says withdrawals are not
available; the person-level Bank card does the same when _every_ account on the record refuses
the flag. The right column is
**Team** (collapsed face-stack, expandable list with avatar, name and role on one line — the
one Adviser first, then Paraplanner / Administrator / Support), compliance and notes. The add form
is a role select and a person picker: Adviser offers only `is_adviser` staff and replaces the
current adviser (who stays on the team as Support); the other roles offer any member of staff.

## 1. Why it is five cards and not one

It was one **Details** card holding twenty-odd fields behind one pencil and one save. Two things
were wrong with that, and they pull in opposite directions.

**A save of any field was a save of all of them.** Correcting a phone number rewrote the whole
record — which is how a details save came to NULL the fact-find answers before the `data.has` rule
was added. That rule now has to hold for five forms at once, which is what makes one action able to
serve all of them: the API's PATCH treats a **present null as "clear" and an absent key as "leave
alone"**, so a form carrying only its own fields updates only its own fields.

**The read view dropped any field with no value.** That reads well for a register of whatever
happens to be recorded and terribly for a section a firm is meant to COMPLETE. A field nobody can
see is a field nobody fills in: `relevant_uk_earnings` went five migrations with no editor at all
while the HMRC relievable-limit check quietly ran on the £3,600 floor for every client whose figure
no onboarding journey had set. So **every section states every one of its fields in both modes**,
with an em dash for the ones nobody has answered.

One section is open at a time. Five simultaneous dirty forms over one record is five ways to lose
an edit; "independently editable" is about what a save TOUCHES, not how many can be open.

## 2. The sections

| Section          | Fields                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | Shown when                                                              |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Contact details  | title, first/surname (or organisation name), email, primary number, secondary number, address                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | always on **Details** (and `all`)                                       |
| Bank details     | UK: account name, sort code, account number, **payment method** (BACS — the free default — or Faster Payment) · International: account name, IBAN, SWIFT/BIC (always SWIFT — nothing to choose) — `Segmented` switcher (UK covers Jersey, Guernsey, Isle of Man, Gibraltar), one set per person (`crm.contacts.bank_account`); `instructions:write`. A UK sort code and account number are checked with the sort-code directory before they are stored: an unknown sort code and an account number that fails the check for that sort code are refused, and if the directory cannot be reached the details can still be saved. The same pair is not asked again. The method saved here is the **default** a withdrawal starts on; the withdrawal itself can still choose the other rail. The applicant and organisation fact-finds ask this same form ([`docs/flows.md`](flows.md)). When the firm has four-eye on for this action the save asks for a justification, the live card stays current, and a pending strip shows the proposal ([`docs/reviews.md`](reviews.md)). | always on **Details** (and `all`)                                       |
| Personal details | dob, gender (male/female), **occupation** (+ specify if Other / previous occupation if Retired), nationalities, national identifier (+ type), tax residency. A National Insurance number is stored compact and refused when it is not one HMRC issues, or when another client in the firm already has it. A passport or a CONCAT on the same field is left as typed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | individuals — Overview (**read-only**) **and** Details                  |
| Pension details  | marital status, employment status, retirement age, annual earnings, employer contributions, tax relief, flexibly-accessed benefits (+ date). A SIPP illustration always asks for the retirement age with nothing filled in, and will not accept one under 55. It writes that age back only when the record does not already hold one of 55 or over — a stored age under 55 is replaced, and a stored age in range is never overwritten. Marital status is filled when the record does not hold it, and is never overwritten. Changing a value that is set is this section. A drawdown illustration for someone already past that age still runs: the quote goes to age 99, and the recorded retirement age stays as it is.                                                                                                                                                                                                                                                                                                                                                   | individuals — Details (and `all`)                                       |
| Employer details | company name, registration number, address, and the employer's **bank account** (`employer_bank_account`, 0271 — the scheme paying employer contributions in; the same inputs, normaliser and live sort-code directory check as the client's own bank card, UK only, no payment method)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | individuals — Details                                                   |
| Vulnerability    | vulnerable, the four FCA drivers, the characteristics under them, duration (temporary / permanent / not established), notes, recorded date. The header banner stays on every page. The pencil opens the drawer (`setVulnerability`); a details save cannot write it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Details (and `all`)                                                     |
| Compliance       | identity / AML / terms ticks (**read-only**); **individuals only:** **Is a US person?** (Yes / No) and **Does this client have a W-9 or a W-8?** (optional, stored as `fatca_status`; blank is normal), **source of funds** (where this client's money came from — client-level, `crm.contacts.source_of_funds`), **source of wealth** (Morgan Lloyd's twelve-member list + a 30-character supporting statement), country and town of birth                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | always (right column, below Team); person-facts for `type = individual` |

**A person's `name` is their parts.** `crm.contacts.name`, the one-line name the dashboard rail,
document requests and ComplyCube (for a person without both parts) fall back to, is first, middle
and surname for an individual. A BEFORE trigger keeps it (`0246_contact_name_from_parts`) on every
write path: journey kickoff, this record's PATCH, a flow's form write-back, the partner API, the
provider pipelines and imports. It acts on an insert, and on an update that changes a part or sets
a new name; an update that touches neither leaves the name as it is. With only one part left, a
name that came from the parts follows them, so a cleared surname leaves it; a name that did not
(a person known by name alone) is kept. Names written before `0246` were not backfilled, so one is
put right the next time its parts change. The Contact details card sends `name` only for an
organisation, whose name is its own field. Screens still show the client as title, first name and
surname (`clientName`).

`0246` also narrows the re-screen trigger (`0223`): a change of `name` counts only when ComplyCube
reads it (an organisation, or a person without both parts). A person with both parts is screened on
the parts, so a fact-find that only adds a middle name re-names them without asking for a new
screen.

The Compliance card also states a JISA's permanent registered-contact relationship. On the
adult's record it reads **Registered contact of {child}**; on the child's record it reads
**Registered contact {adult}**. Both names link to the other CRM record. The directed
`crm.relationships` edge runs adult → child with `relationship_type = 'registered_contact'` and is
created in the same transaction as the JISA's `account_contacts` rows. The account role remains
the authority for what the adult may do; the relationship is the person-to-person explanation.

A registered contact has no account of their own, so the clients listing does not put the child's
account or AUM on the adult. It does count `represented_account_count` for status: once the adult
acts on a JISA they are Active rather than permanently appearing as a Lead merely because the
account correctly belongs to the child.

**Service is gone from the record.** It is an ACCOUNT-level fact — a client can be advised on one
account and execution-only on another — so one value on the person was either a summary that could
be wrong or a default nothing enforced. The column and the listing column stay.

**Compliance sits in the right column, below Team, beside the figures — not in a stack that
stretches the name card.** The page is **two flex columns** (`lg+`; one column on tablet/mobile).
CSS grid rows share height across columns, so a tall main column opened a gap between Compliance
and the rail even with `items-start`. Flex columns size independently: identity + details on the
left, team + compliance + the rail on the right. Below `lg` the wrappers are `display:
contents`, so the five blocks are one column in the order identity → team → compliance →
main → rail. The three ticks are still **read-only in both modes**: `crm.kyc_checks` is written
by the screening provider and the flow engine, and an adviser ticking "AML done" by hand would
put an unevidenced clearance on a regulated record. Screening (`compliance.*`) is Graphene's own
responsibility as platform provider (`docs/aml.md`) — a firm sees the outcome, never the adjudication. The
overview register is a **single column** (the other sections use
two/three). **Source of wealth is a `SelectMenu`**, Morgan Lloyd's own twelve-member list, followed
by the client's required supporting statement. The pair maps to `cam_sourceofwealth` /
`cam_sourceofwealthstring`. The ticks stay register-only (set during onboarding). **US person, source of wealth, country of birth and town of birth are individuals only** — none of
them is a fact about a trust or a company, so an organisation's Compliance card is the three
ticks and no pencil. Occupation lives on **Personal details** — it is who they are, not a
compliance tick. **A `narrow` register WRAPS its values; a `wide`/`thirds` one ellipses them**
(`TruncatedText`). The rail is 16–22rem and "Investment Income or Sale of Investments" does not
fit one line of it — but a rail is the one place the value cannot be read anywhere else on the
page, so clipping it there hides the whole answer behind a hover and reads as a shorter answer
rather than a hidden one. The wide sections keep the ellipsis: a wrapped value ragged their
columns. The row is a grid, `minmax(min-content, auto)` label + `minmax(0, 1fr)` value, so the
longest label can no longer decide how little room every value gets. **In edit mode the register
renders INSIDE the form, above Save/Cancel** — Compliance is the only section that shows a form
and a register at once, and rendering it after the form left country and town of birth sitting
below the buttons, reading as having fallen out of the card. The
register states **Is a US person?** (Yes / No) and **W-9 or W-8** as two separate facts, and the same controls edit them.
Source of funds sits beside source of wealth: it is where this person's money came from, asked
once on the client, not a field of one account. The two US facts are independent: a blank US-person
answer is never sent, and choosing a W-9 or W-8 stamps `fatca_certified_at` with today. A W-9 with
No, or a W-8 with Yes, is refused. When the answer is W-9 or W-8, the signed form is uploaded on the
record (`POST /crm/contacts/{uid}/fatca-document`, a PDF or an image) and filed in the client's
documents as `fatca_form`. A change of answer unlinks it (`crm.contacts.fatca_document_id`, `0261`);
the document stays filed. That upload also completes an onboarding step still asking the client
for the form, and an answer changed to anything but W-9 or W-8 withdraws that step
(`docs/flows.md`). Passport number and
country are **not** on this record — nationality and tax residency already live on Personal, and
a passport is an SEI enrolment fact. **The `terms` tick is derived** from
`terms.acceptances` (`0179`): passed when the contact has accepted every currently required
onboarding version. The Compliance page also lists those acceptances (title, version, when,
channel). See [`docs/terms.md`](terms.md).

The pension section's heading is always **Pension details**, not the wrapper word. A client can
hold TPPENSION and SIPP at once, so naming the card after the first pension account's
`product_type` made the heading a lie. `pensionLabel` still names the illustration button.

## 3. Pension and Employer are unconditional now, and what still isn't

Pension and Employer details used to be hidden — the Pension card only for a client whose accounts
included a `platform.products.is_pension` wrapper, the Employer card additionally behind the
"Receives employer contributions" answer — on the theory that asking a GIA-only client for their
salary is asking for a fact the firm has no business holding. Both display gates are gone: every
individual's Details tab now always shows Contact, Bank, Personal, Pension and Employer, with an em
dash for whatever nobody has answered, same as the rest of the record (§1). `employer_contributions`
is still a real field — the Switch on the Pension form still saves it, and it still reads back on
the register (`register` row "Employer contributions") — it just no longer decides whether the
Employer card exists.

**This was a deliberate reversal, not a bug fix — read this before re-adding either gate.** The
former pension gate replaced `isPensionProduct`, a hardcoded wrapper set with **nine copies across
the estate that did not agree** (`contributions`, `matching`, `beneficiaries` and
`accounts/transactions.ts` carried `{SIPP, PENSION, SSAS, TPPENSION}`; `routers/crm`,
`routers/illustrations` and `morgan_lloyd/sync` omitted `TPPENSION`; `flows/planning` tested
`{SIPP, TPPENSION}` plus a substring) — `0141` made `platform.products.is_pension` the one correct
column after finding it had been false for every pension since `0042`. That correctness effort is
moot for the UI now, but the column and its history remain load-bearing for everything below.

**The integration gates were never the display gate, and still apply unchanged.**
`providers.attach_scheme_administrators` maps Morgan Lloyd onto SIPP only (`is_pension` is
true for TPPENSION too, and using it as the filter is what tagged a third-party pension as
one Morgan Lloyd administers). `morgan_lloyd/sync` excluding `TPPENSION` from `cam_createscheme`,
and `crm`'s policy-valuation and drawdown gates, all still test their own wrapper sets — a
third-party pension is administered elsewhere and is not a Morgan Lloyd scheme regardless of
what the Details tab shows. What is shown on a record and what is sent to a custodian remain
separate decisions; only the first ever moved, and now the first has no gate at all.

## 4. The controls are the shared ones

`pkgs/ui` already owns every control this record needs, and a second implementation of "type an
address" is a second set of behaviours for one concept.

- **`AddressLookup`** (Postcoder, through the portal's `/api/postcoder` BFF — the key lives only on
  the API, since the upstream takes it as a query parameter). The display matches onboarding: a
  country picker beside the lookup, then a one-line summary of the chosen address (Edit / Clear),
  with the line fields only when someone asks to type. A BFPO or an overseas address is not in the
  database and must still be recordable. One trap, mapped in one place: **Postcoder says `town`
  where our column says `city`**, and `Address` has no `city` key — so a naive lookup filled every
  field except the town, which reads as Postcoder being flaky rather than as a key name.
- **`SelectMenu`** for the closed lists (title, marital status, employment status, tax relief, **source of wealth**). They were
  native `<select>` elements in `FIELD_CLASS`, which is the OS popup the design language forbids. Title, marital status and employment status are the shared field types (`title`, `marital`, `employment`); source of wealth is
  Morgan Lloyd's twelve-member list, and `source_of_wealth_detail` is always required and holds the
  client's supporting statement. The provider codes for these lists are in
  [`client-vocabularies.md`](client-vocabularies.md).
- **`Segmented`** for gender (the `gender` field type: male / female) and for bank details (UK / International). Content-width,
  not row-width: the control is already `inline-flex`, and `w-full` stretched two short labels across
  the card. The column is those two values. The control renders buttons, so the value
  posts through a hidden field.
- **`MultiSelect`** for nationalities. It is `text[]` — a person can hold two passports — and
  stores **ISO codes** from `ref.countries` (0050). It was a free-text box, so a firm typed
  "United Kingdom" into a column holding `GB`: unjoinable to the reference table and unusable at
  every boundary that takes ISO (ML, CTC and ComplyCube all do). The action reads it with
  `getAll`; a single `get` would keep only the first. The same control records the 24 FG21/1
  vulnerability characteristics (grouped by driver) in the vulnerability drawer; `portal={false}`
  keeps the panel inside the sheet's focus trap.
- **`MultiSelect`** for nationality and tax residency — the two ISO-code arrays use the same
  searchable field and selected-value treatment, and neither discards a second country. The
  actions read both with `getAll`, filtering the blank sentinel that lets an adviser clear every
  selection. Values come from `ref.countries`, never typed names.
- **`MoneyInput`** for annual earnings — the one money field (`docs/design-language.md`). It posts the
  grouped text it renders ("84,250.00"), which the action parses.
- **`Switch`** for employer contributions and flexibly-accessed benefits — switch on the left of the
  label, not a checkbox and not pushed to the right edge. Flipping flexibly-accessed benefits still
  reveals its own date field immediately (the vulnerability-flag pattern); employer contributions no
  longer gates the Employer card's existence (§3) but still saves as a normal field. The posted value
  is a hidden field because Switch renders a button.
- **`InputGroup`** for email and the primary number, with the icon inside the control and
  `type="email"`/`"tel"` for the keyboard and the browser's own check. The action re-validates the
  email, because a client can defeat either.
- **Mobile and phone are two masked `tel` inputs**, posted as `mobile` and `phone`. A UK number
  typed with the trunk prefix (`07812 345678`) is stored as `+44` and those ten digits. Mobile is
  individuals only. Phone is shown for everyone (an organisation's switchboard).
- **Country of birth reads `countries`, not `nationalities`** — `resolveOptions` prefers the
  ADJECTIVE for the latter, so a picker fed from it would offer "British". You are born in a country.
- **Source of wealth is Morgan Lloyd's own twelve-member list**, a `SelectMenu` on the Compliance
  card (and a `select` on the onboarding fact-find). `_SOURCE_OF_WEALTH` sends the vendor's int only
  on an exact label match. `source_of_wealth_detail` is a separate, always-required statement of at
  least 30 characters and is sent as `cam_sourceofwealthstring`; older records without one fall
  back to their selected label at that boundary.
  Individuals only, with the W-8 declaration and place of birth: `updateClient` ignores those keys
  on an organisation even if a form posts them.

**A client has a mobile and a phone.** `0240` replaced the labelled `phone_numbers` list with two
text columns. `mobile` is the number every custodian can store. `phone` is the other number:
Morgan Lloyd's `telephone1`, a second SEI contact-details number, and Seccl's rarely populated
`phone` object. Fax and pager are not stored. Seccl and SEI enrolment send the mobile only;
Morgan Lloyd sends `mobile` as `mobilephone` and `phone` as `telephone1`. `telephone2` is not sent.

The onboarding question key stays `phone` for the mobile, so an in-flight answer still binds, and
`other_numbers` is the other number (a single `phoneNumber`, written to `phone`). Letters keep
`{{ client.phone }}` as the mobile. `{{ client.telephone }}` is the other number.

**Mobile is individuals only; phone is shown for everyone** — an organisation has a switchboard
and no mobile of its own. The register shows Phone only when one is set.

## 5. THE TWO ALLOWLISTS, and the bug they hid

A field is patchable only if it is on **both** `routers/firm.py::UpdateContact` and
`contacts.py::_UPDATABLE`. Only one of them fails loudly.

Pydantic's default is `extra='ignore'`, so a column in `_UPDATABLE` but not on the model is dropped
on the way IN: it never reaches the allowlist to be refused, the PATCH answers **200**, and the
record reads back unchanged. That is not hypothetical — `employer_name` and `relevant_uk_earnings`
were added to `_UPDATABLE` alone, so the Employment fields posted, reported success and saved
nothing. `tests/test_contacts.py::TestTheTwoAllowlists` pins the two against each other in both
directions, with the deliberate exceptions named.

`dob` and the identifier/residency block were never editable at all: onboarding wrote them and
nothing could correct one, so a date of birth typed wrong on a fact-find needed SQL — on the column
a pension projection reads.

**There is a third allowlist, and it is the one the fact-find writes through.**
`flows/step_handlers.py::_write_contact_patch` decides whether a mapped onboarding answer becomes a
column or a dotted key under `crm.contacts.data` — and `data` is what the record shows as "N more
from the fact-find", with the key's last segment as its label. `0139` gave the Employer card
`employer_contributions`, `employer_name`, `employer_registration_number` and `employer_address`,
and neither the onboarding seed's `EMPLOYER_MAPPING` nor that allowlist was told: the employer
step kept mapping to `data.pension.employerContributions.*` (where the legacy app kept them), so a
fully answered employer step showed three em dashes on the card and `HasEmployerContributions: Yes`
in the overflow. `source_of_wealth_detail` had the same gap from `0116`. Both now map to the
columns (`employer_address` merges like `address` and `bank_account` do), the employer step asks
the company's address, and `0210` backfills the rows written the old way — copying into the column
where it is empty and removing the copied keys from `data`. The scheme's bank details went the
same way last: `0271` gives them `employer_bank_account` (the `bank_account` shape, through the
same normaliser and directory check), retargets `EMPLOYER_MAPPING`, adds the column to that
allowlist's jsonb set, and backfills the rows written as `data.pension.employerContributions.{sortCode,
accountNumber}` — which the record had been listing as "2 more from the fact-find". When a column
is added for a fact the fact-find asks, add it to that allowlist and retarget the mapping in the
same change, or the answer lands one layer down and the card stays blank.

## 5b. The ACCOUNT record wears the same layout

`/accounts/{uid}` is the client record's shape, deliberately, because the two are read the same way
and looked like different products. What changed (`book/accounts/account-detail.svelte`):

- **Two flex columns from `lg`, not one stack.** The account's own facts on the left (Account info,
  Bank details, Contributions), the arrangements standing on it on the right (Fees, Regular payments
  & withdrawals, Beneficiaries, Recent instructions) in a `lg:w-[22rem]` rail. Flex rather than grid
  for the reason above — grid rows share height across columns. Below `lg` both wrappers are
  `display: contents`, so it is one column: facts → arrangements. It was a single stack of
  equal-weight cards, the same diagnosis `docs/ux-audit.md` made of the client record.
- **Four figures on tiles, above the band**: Value · Cash · Book cost · Unrealised gain / loss — the
  client record's row, in the client record's words. They were three bare `Stat`s in the top-right
  corner of the header card, which is exactly the layout the client record considered and rejected,
  and "Growth" was a second name for a figure already called an unrealised gain. `StatTile`, not a
  hand-rolled Card + Stat, which is that component's own standing rule.
- **The tiles sit OUTSIDE the band**, so Holdings and Transactions keep them. Those are the bands
  where "what is this account worth" is the question the reader is holding while they read the rows,
  and the old header carried the figures there.
- **Book cost is all-or-nothing.** `bookCost` rides each holding and the custodian does not always
  give one, so a sum over the rows that have one, set against a value for all of them, reads as a
  gain nobody made. Every holding or an em dash. **Unrealised gain is the ACCOUNT's own `growth`**,
  not a sum of the holdings' — the two are struck from different places, and summing here would put
  a second answer to one question on the same screen.
- **The identity line is a copyable chip row**, like the client record's email/mobile/address:
  reference · owner · adviser · opened · custodian, xs, an icon each, hover reveals a copy
  icon. It was a run-on muted sentence of dot separators with no way to lift the reference out —
  the one value on that card anybody needs to paste somewhere else. The adviser keeps its words
  ("Adviser: …") because an owner and an adviser are both a person's name.
- **Every card is `glass`.** The four body cards were plain `Card`, so the band read as a different
  material from its own header and from the client record beside it.
- **Overview no longer takes the viewport.** It is a record page and the PAGE scrolls it; only a
  band whose content is a table (Holdings, Transactions) still owns its own scroll region. A rail
  and a stack of cards inside a viewport-tall pane gives the reader two scrollbars and clips
  whichever column is longer.

**The heading's acts live in an outline Actions menu** — Payment, Withdraw, Ad hoc fee, Change
model, Transfer, and (when they apply) Drawdown and Push to administrator. Withdraw starts on the
payment method saved on the bank account (the account's own, else the client's) and can use the
other: BACS is free, Faster Payment shows SEI's £0.50 fee, and passing that fee on to the client
can be switched off so the firm pays it for that withdrawal only. The fee block states both sides
— **Total to be debited** and **Recipient receives** — because the fee sits on top of the amount
(or with the firm), never out of what the recipient gets. The pay-to bank details can be changed
from inside the Withdraw drawer (Change bank details → the same fields and the same door as the
overview pencil, `PUT /crm/accounts/{uid}/bank-accounts`, four-eye included), so paying a
withdrawal somewhere new does not mean closing the form first. An international destination
stays on SWIFT. A payment in may be **backdated** — the client paid last week and the record is
made now — up to a year (`instructions.BACKDATE_LIMIT_DAYS`); the drawer says "This payment is
backdated" rather than refusing, and the API holds the floor. A withdrawal or fee still may not
carry a past date. Regular payment and
Regular withdrawal sit after a separator, named as standing schedules so they cannot be read as
the one-off money acts above. A row of outline buttons under the name made the first thing on the
account a list of things you could do to it. They stay one click away; a closed menu paints
nothing at SSR, so tests pin the trigger (`Account actions`) and read the items off source.

**A standing payment or withdrawal is one line in that rail**: the amount, how often, and the next
date. The method is not on the line — a missing one was an em dash beside "next". The pencil opens
an update of the amount, the frequency and the next date; cancelling the instruction lives in that
dialog, not as a cross on the row. A pension still cannot have a regular withdrawal set from here.

**There is no avatar.** The client hero carries one because a client is a person; initials on an
account would state something untrue about what the record is.

## 6. Beneficiaries (`crm.beneficiaries`)

On the ACCOUNT, pension wrappers only. A pension's death benefits sit outside the estate, so they
are not directed by a will: the member's expression of wish is the only instruction the scheme
administrator has. An ISA or a GIA passes under the will and through probate, so offering a
nomination there would invite a firm to record a wish with no effect.

It hangs off the account rather than the contact because that is the grain the fact has:
`cam_createbeneficiaries` is addressed by `schemeReference`, a member with two pensions can nominate
differently on each, and an expression-of-wish form is signed per scheme.

**`crm`, not `platform`** — Morgan Lloyd publishes a conformed `platform.beneficiaries` into the
lake, and CLAUDE.md records that it deliberately does not merge into OLTP. Taking that name for our
firm-authored nominations would leave the two to be told apart by whoever is reading.

**Add and remove, no edit.** A nomination is replaced by withdrawing it and making a new one, which
is what an expression-of-wish form is — the member signs a fresh one. An edit in place would let a
share move under a name with the audit row saying only "updated". The removal is a hard DELETE: a
superseded wish is not history to keep but a statement the member has WITHDRAWN, and a retained row
is one careless read from paying out to somebody they removed. The audit trigger keeps the deletion.

**Proportions are not constrained to total 100, deliberately.** The obvious rule — assert the set
totals 100% — makes the FIRST insert fail: a 50% beneficiary added to an empty account, refused for
totalling 50. Over 100 is unambiguously wrong at every point in the edit; under 100 is only wrong
once the firm says it has finished, which the write path cannot know. So the API refuses an
over-allocation (409, **naming the headroom** — "that is too much" without the number is a refusal
an adviser has to do arithmetic to act on) and the panel states the running total in words. A
hundredth of a percent of slack, because three 33.333% nominations total 99.999 and reporting that
as a shortfall would send a firm looking for a fourth beneficiary that does not exist.

`title` is ours: the vendor takes one `cam_nameofbeneficiary` and has no title field, and dropping
what the client wrote because one vendor has no column would lose it. It is concatenated at the
boundary, never stored joined. The panel asks for it with the same `SelectMenu` and the same closed
list (`PERSON_TITLES`, the `title` field type, the same list as the client record and the onboarding
applicant form; what each custodian actually accepts is in [`client-vocabularies.md`](client-vocabularies.md)).

The SIPP account record's **Sync to Morgan Lloyd** action (and the client record's matching one)
is gated `contacts:sync_morgan_lloyd` — not `contacts:write`. Onboarding `setup_account` already
runs the same push when the product names Morgan Lloyd as scheme administrator; the button is
the retry when that report failed (missing NI number, scheme-type pairing, credentials). It
pushes each nomination through
`cam_createbeneficiaries` as the last step after the scheme exists — `schemeReference` is how the
vendor addresses them. Idempotent via `platform.links`
(`entity_type='beneficiary'`). The account panel does not collect an address on a nomination, so
the member's client address is the fallback; missing both is a refusal naming the fields, not an
invented address on a death-benefit record.

The SIPP record itself names **Scheme administrator** next to Custodian (from
`product_platforms.role`, displayed via `scheme_administrator_name`), and the header states
**Crystallised** / **Uncrystallised** plus Cash in both places (custodian lake positions +
Morgan Lloyd's scheme bank from `provider_positions`) when the administrator valuation has those
figures.
