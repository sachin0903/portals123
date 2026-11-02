# Morgan Lloyd + CTC — endpoint coverage review

Audit of `pkgs/morgan-lloyd` against the operation catalogue in
[`api-reference.md`](api-reference.md), done to work out what is needed to seed a complete
client in the dev CRM (contacts → advisers → schemes → beneficiaries → transfers →
contributions → investments → drawdown).

**Headline: the client is broadly complete — 65 methods, 51 distinct actions across 6
modules. The bigger problem is that `api-reference.md` names operations differently from
the client, and the CLIENT's names are the ones verified against the live API.** Treat the
catalogue as a map of capabilities, not a source of action names.

## 1. The naming divergence

These are the same operation under two names. The left column is what `api-reference.md`
calls it; the right is what `pkgs/morgan-lloyd` actually POSTs — and, where noted, what is
confirmed working against `cliftonassetdev`.

| `api-reference.md`                      | client (authoritative)        | status                                                                             |
| --------------------------------------- | ----------------------------- | ---------------------------------------------------------------------------------- |
| `cam_createbenefitapplication`          | `cam_setmemberdetails`        | **verified live** — returns `benefitApplicationId` + `ssoUrl`                      |
| `cam_retrievememberdetails`             | `cam_memberdetails`           | —                                                                                  |
| `cam_getschemebyschemref`               | `cam_getscheme`               | **verified live** — returns "Scheme … not found" for absent refs                   |
| `cam_getalltransfersbysceme` (sic)      | `cam_getalltransfersbyscheme` | doc has a typo                                                                     |
| `cam_gettransfer`                       | `cam_getpensiontransfer`      | —                                                                                  |
| `cam_getbeneficiariesbyschemeref`       | `cam_schemebeneficiaries`     | —                                                                                  |
| `cam_getbenefitevents`                  | `cam_schemememberevents`      | —                                                                                  |
| `cam_createbeneficiary`                 | `cam_createbeneficiaries`     | plural                                                                             |
| `cam_createpensioncontribution`         | `cam_createcontribution`      | —                                                                                  |
| `cam_updatepensioncontribution`         | `cam_updatecontribution`      | —                                                                                  |
| `cam_updatecontributionpension_status`  | `PATCH cam_contributions(id)` | not an action at all — a plain entity PATCH of `cam_status`                        |
| **"Create Investment"** (section title) | `cam_createwrapper`           | **twelfth, found Aug 2026** by parsing the page rather than reading it — see below |

The twelfth is worth its own note because it is the only one where the divergence is in a
SECTION HEADING rather than an action name, which is why reading the catalogue never caught it:
the page's **Investments → Create Investment** POSTs `cam_createwrapper`. §2 below already
recorded that `cam_createinvestment` does not exist in the org (`$metadata` disproved it); this
is where the operation actually lives. `manifest.ACTION_ALIASES` carries the mapping.

Action names are **case-sensitive** in Dynamics. `cam_CreateFinancialAdviserContact` is the
one mixed-case action; everything else is all-lowercase.

## 2. Genuinely not implemented

Six operations in `api-reference.md` have no counterpart in the client. **None blocks the
seed** — `$metadata` disproved the one I thought did:

| Action                                                       | Why it matters                                                                                                                                                                                                                                                                             |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| ~~`cam_createinvestment`~~                                   | **Does not exist.** Corrected by `$metadata`: no such action in the org. The investment/valuation step is **`cam_createpolicyvaluation(policyValuation, provider)`**, which IS implemented — and its `provider` parameter is where the `6ac39921-…` GUID from the API Details doc belongs. |
| `cam_getbanktransactions`                                    | Bank transactions by scheme ref (inbound/outbound) — reconciliation input.                                                                                                                                                                                                                 |
| `cam_submitillustration`                                     | Attach a CTC illustration to a scheme (`cam_scheme`, `illustrationdata`, `illustrationType`).                                                                                                                                                                                              |
| `cam_updatebenefitillustration`                              | PATCH an illustration template onto a benefit application. Note `benefits.py` DOES implement `update_benefit_application_illustration_template`, so this may be a naming pair rather than a gap — confirm.                                                                                 |
| `cam_getschemerevidate`                                      | Scheme review date.                                                                                                                                                                                                                                                                        |
| `cam_gettransferbypolicyno`                                  | Transfer lookup by policy number.                                                                                                                                                                                                                                                          |
| `cam_updatetransfer`, `cam_updatecontributionpension_status` | Transfer / contribution status writes.                                                                                                                                                                                                                                                     |

## 3. What the client has that the catalogue omits

`api-reference.md` is missing 22 actions the client implements — most of the **fact-find**
surface (`cam_createclientfactfind`, `cam_getclientassets/liabilities/relationships`,
`cam_createupdate{account,liability,personalasset,relationship,softfacts}`), plus
`cam_createwrapper`, `cam_updatemvpprofile`, `cam_updateschemevaluation`,
`cam_getaccounttransactions` and `cam_illustration`. The doc needs updating from the client,
not the other way round.

## 4. Entity creation order (for the seed)

Derived from the required fields of each call:

```
1. adviser firm (account)      pre-existing: 6ac39921-… "Graphene Platforms Limited"
2. adviser contact             cam_CreateFinancialAdviserContact
                                 accountId(firm) + title + firstName + lastName
3. client contact              cam_createcontact
                                 as_contactstatus, cam_title, first/lastname,
                                 emailaddress1, cam_nationality, gendercode,
                                 cam_ninumber (must be a FORMAT-VALID NINO —
                                 QQ123456C is rejected), address1_line1/city/
                                 postalcode, cam_birthdate
                               -> response.contactid  (nested, lowercase)
4. scheme                      cam_createscheme
                                 contactId + schemeType 16 + sippType 809130016
                               -> schemeReference (e.g. MLSIPP/00001)
5. beneficiaries               cam_createbeneficiaries
6. contribution / transfer     cam_createcontribution / cam_createtransfer
7. valuation / investment      cam_createpolicyvaluation(policyValuation, provider)
                                 provider = 6ac39921-… (Graphene Platforms Limited)
9. drawdown                    cam_setmemberdetails -> ssoUrl (DCube)
```

### The blocker, restated

A contact must carry `_cam_financialadviser_value` pointing at the firm, or step 4 fails:

```
cam_createscheme → 403 0x8004f502 … does not have create permissions to a
                   ML Team secured field on entity Scheme
```

Evidence it is the LINK and not the permission: contact `a2c48581-…` (Dobroslava Houben,
pre-existing, `_cam_financialadviser_value = 6ac39921-…`) reaches the create path and returns
the business rule _"A scheme of this type has already been setup for the contact"_. Contacts
we create have that field `null` and 403. Owner, owning team and owning business unit are
identical across all three, which rules out ownership.

> **Superseded, 09 Aug 2026 — it IS the permission, and the evidence above was weaker than it
> reads.** That contact answered a business rule, which proves the create path was REACHED, not
> that a scheme could be created; no scheme was.
>
> What we have now is stronger, because it is a scheme that exists. `raw.schemes` holds three, and
> **two were created by our own service principal** — `d3ea67c4-0059-f111-a825-002248c7d4b8`, the
> exact caller id in the 403 — as `MLSIPP/02037` (30 Jul 2026) and `MLSIPP/02038` (3 Aug 2026),
> both with `as_schemetype = 16` and `cam_dsipptype = 809130016`, which is the pairing we send.
> `build_scheme` has not changed in git since. So the principal could create schemes six days ago
> and cannot now.
>
> Four hypotheses tested and eliminated on 09 Aug:
>
> | Tried                                                     | Result                      |
> | --------------------------------------------------------- | --------------------------- |
> | Contact WITH the adviser firm + adviser contact           | same 403                    |
> | Contact WITHOUT either (an older unattached test contact) | same 403                    |
> | All 28 documented `schemeType`/`sippType` pairs           | 14 invalid, 14 the same 403 |
> | With the optional `benefitApplicationId` from a real one  | same 403                    |
>
> The pairing space is therefore understood — 14 pairs are accepted as valid and every one of them
> is then refused on field security — so no value fixes it. **A field on entity `Scheme` appears to
> have become ML-Team-secured between 3 and 9 August, or the principal's field-security profile /
> team membership changed. That is a question for Clifton, with dates.** Everything downstream of
> the scheme (beneficiary, transfer, investment, contribution, and the valuation that needs the
> investment's policy) is blocked behind it; contact, benefit application and the contact PATCH all
> succeed.
>
> **The secured fields are named, so the ask can be specific.**
> `EntityDefinitions(LogicalName='cam_scheme')/Attributes?$filter=IsSecured eq true` returns **27**,
> and **13 are fee fields** — `cam_feetype`, `cam_feesbreakdown`, `cam_mlfeetype`, `cam_feepayer`
> (Monthly Fees Setup), `cam_dsippfeebasis`, `cam_mlfeearrears`, `cam_nextfeecheck`,
> `cam_cfxfeestatus`, `as_feestatus`, … — plus **`cam_mlteam`**, which is what the error's phrase
> "a ML Team secured field" is naming. In Dataverse a secured field is writable only through a
> Field Security Profile, so the question for Clifton is not "please grant access to Scheme" but:
>
> > Service principal `d3ea67c4-0059-f111-a825-002248c7d4b8` created `MLSIPP/02037` (30 Jul) and
> > `MLSIPP/02038` (3 Aug) and now gets `0x8004f502` on a ML Team secured field on entity Scheme
> > for every valid `schemeType`/`sippType` pair. Has its Field Security Profile changed, or has a
> > field that `cam_createscheme` writes been added to the ML Team profile?
>
> Note we send NO fee fields — `build_scheme` deliberately omits all three optional percentages —
> so whatever the action writes, it writes of its own accord.

> **RESOLVED, 05 Sep 2026 — the grant was made, and both blocked permissions are now open.**
> `cam_createscheme` succeeds: the live lifecycle suite created `MLSIPP/02113` and `MLSIPP/02114`
> with the same `schemeType=16` / `sippType=809130016` pairing that returned `0x8004f502` on
> 9 August, from the same service principal. **`cam_CreateFinancialAdviserContact` is granted
> too** — it previously answered `does not have CreateAccess right(s)`, which is why
> `app/integrations/morgan_lloyd/sync.py` documents "it does not create the adviser as a Dynamics
> contact" as a deliberate omission.
>
> So everything downstream of the scheme is unblocked and is now exercised end to end against the
> org by `moon run morgan-lloyd:integration-test` (`docs/vendor-integration-tests.md`): adviser,
> client, scheme, policy, valuation, contribution + amend + stop, drawdown SSO, illustration
> attachment, transfer + update + withdraw, beneficiary + remove. Sixty assertions, and a re-run
> creates nothing — verified by counting `contacts` / `cam_schemes` / `cam_policies` either side.
>
> The two lessons that outlived the blocker:
>
> - **A vendor permission being granted is a state nothing observes.** This sat blocked for a
>   month while the code was correct all along. That is the same argument the inverted test in
>   `test_live_contract.py` makes for the 85 forbidden tables, and it is why these suites exist.
> - The field-security diagnosis above (27 secured fields, `cam_mlteam` the one the error names)
>   is kept because it is how the ask was made specific enough to action. It was never a code
>   defect and no code changed.

### The action envelope is not consistent — check both levels

Assuming a shape here has cost two bugs in opposite directions, so `populate_test_data.envelope_value`
looks in both and prefers whichever carries the key:

| Action                 | Where the identifier is                                                               |
| ---------------------- | ------------------------------------------------------------------------------------- |
| `cam_createcontact`    | NESTED under `response` (`response.contactid`)                                        |
| `cam_setmemberdetails` | TOP LEVEL (`benefitApplicationId`, `ssoUrl`); `response` is the status envelope alone |

Reading the top level alone lost the contact id, which is how `sync-to-morgan-lloyd` created a
contact, discarded its id and duplicated the person on every retry. Reading the nested object
alone then lost the benefit application's id and its `ssoUrl` — reported for a whole run as "no
ssoUrl in answer", with drawdown skipped for want of an id the vendor had returned.

### The adviser is a PAIR, and both halves are entity references

`cam_financialadviser_Account` (the firm, an `accounts` row) and `cam_FinancialAdviserContact`
(the adviser, a `contacts` row) are required TOGETHER. Learned in two steps, neither visible in
the field table:

```
firm alone          → 400 0x80040224 Financial Adviser contact must be populated
                      for the adviser firm
contact as a GUID   → 400 0x80040224 Invalid Format for cam_FinancialAdviserContact
```

The manifest types both `lookup`, and that is the rule: `/accounts(<guid>)` and `/contacts(<guid>)`.
The conditional only bites once the firm IS configured, which is why omitting both worked for
months and created every client unattached. The adviser contact is discoverable —
`cam_getadvisercontacts(firm)` returns it — so it need not be configured when a firm has one.

### SOLVED — via `$metadata` (see §6)

The parameter is **`cam_financialadviser_Account`** on `cam_createcontact`, and its value must
be an OData entity reference, not a bare GUID:

```json
"cam_financialadviser_Account": "/accounts(6ac39921-d8b7-f011-bbd3-7ced8d6b85ff)"
```

`accounts(<guid>)` (no leading slash) is also accepted. A bare GUID, a braced GUID and an
uppercased GUID all fail with _"Invalid Format for cam_financialadviser_Account"_ — which is
itself the useful signal: once the NAME is right the error changes from
_"Error identified in Payload"_ (unknown key) to a format complaint.

There is also `cam_FinancialAdviserContact` for linking the individual adviser rather than
the firm.

Previously ruled out (all wrong because the real name carries the `_Account` suffix):

- `cam_createcontact` parameters `provider`, `financialAdviserId`, `cam_financialadviser`,
  `adviserId`, `financialAdviser` — all rejected ("Error identified in Payload"), so the
  action validates against a fixed allowlist.
- `PATCH contacts(<id>)` with `cam_FinancialAdviser@odata.bind` / `cam_financialadviser@odata.bind`
  — rejected by the same payload validation.
- `cam_CreateFinancialAdviserContact` — returns `does not have CreateAccess right(s)`.

`cam_createupdaterelationship` is NOT the mechanism — it takes fixed Relationship Type GUIDs
for personal relationships (Child/Spouse/Parent) between contacts.

## 5. CTC illustration engine

Not a Dynamics endpoint; a separate XML-over-HTTP service. Documented here because it sits
in the same flow.

| Environment | URL                                                                   |
| ----------- | --------------------------------------------------------------------- |
| staging     | `https://camtest.ctc.uk.com/Pages/Remote.aspx?wci=RunQuote&nodb=true` |
| production  | `https://cam.ctc.uk.com/Pages/Remote.aspx?wci=RunQuote&nodb=true`     |

`CTC_ILLUSTRATION_URL` defaults to camtest; local `.env.local` sets it explicitly. No
credentials — identified by `<agent_name>` (`CTC_AGENT_NAME`) with `nodb=true`.

**Verified working** (2026-07-29) on both hosts, with `content-type: text/xml; charset=utf-8`
— our builder's own output returned
`https://camtest.ctc.uk.com/PDFPortalArchive/GRAPHENE-DIFF.pdf`. So `document_location` **is
a URL**, which closes the open question in CLAUDE.md: the fetch-and-file path is correct and
`stored: false` should be rare.

Two things learned the hard way:

- CTC signals application errors with **HTTP 200 + an HTML page** whose only diagnostic is
  `alert('An error has occurred. [EXC]')`. `parse_response` extracts that text; it also fails
  closed on any root element that is not `<message>`, because an XHTML error page is
  well-formed XML and would otherwise be read as a silent success.
- `product_ref` is firm-specific. `SIPPEN` works for us; `QUALI` (from Clifton's own working
  example) returns `[EXC]` — ask whether we should be entitled to it.

Known defect: `xml_builder` makes `annuity_basis` **mandatory** for pensions, but CTC's own
working Drawdown example omits it entirely (Clifton's Accumulation example includes it). The
rule should be conditional on `quotation_type`, or dropped.

## 6. Machine-readable source of truth: `$metadata`

**Do not scrape the docs site.** Dataverse publishes the full OData CSDL (EDMX) for this
org, which is authoritative, versioned with the environment, and complete:

```
GET https://cliftonassetdev.crm11.dynamics.com/api/data/v9.1/$metadata
Accept: application/xml          <-- REQUIRED
```

Our client sends `Accept: application/json` for everything, so this returns **415 "A supported
MIME type could not be found"** until the header is overridden. 31 MB of XML.

What it gave up immediately:

- **86 `cam_*` actions** exist in the org; the client implements 51. The catalogue in
  `api-reference.md` lists 47 — so BOTH our documents undercount, and metadata is the only
  complete list.
- **Every action's exact parameter names and types.** This is what solved §4's blocker in one
  read after five wrong guesses. `cam_createcontact` takes **35** parameters — we send 13.
- `cam_createscheme` takes **8**, including `beneficiaryFund` (Boolean) and
  `benefitApplicationId` (String) that we never send, plus the three fee percentages.

Caveats: CSDL marks every action parameter `Nullable="true"` regardless of business
requirement, so it does **not** tell you what is mandatory — the docs site and trial-and-error
still own that. It also carries no enum _labels_ (option-set values are bare `Int32`), which is
why `morgan_lloyd/enums.py` remains necessary.

### Generating a client or OpenAPI spec

Given the CSDL, the realistic options in preference order:

1. **Generate typed stubs from CSDL directly.** Parse the `<Action>` elements and emit one
   Python method per action with typed keyword arguments. ~86 methods, fully mechanical, and it
   would keep `pkgs/morgan-lloyd` honest — a CI check could fail when the org gains an action
   we do not implement. This is the same pattern `pkgs/seccl` already uses (build schemas from
   the vendored Postman collection, fail the gate on drift).
2. **CSDL → OpenAPI.** Microsoft's `Microsoft.OpenApi.OData` converts EDMX to OpenAPI 3, and
   there are OSS equivalents. Viable, but Dataverse's full surface is enormous — it would need
   filtering to `cam_*` plus the handful of entity sets we read, or the spec is unusable.
3. **Scraping `dev.api.morgan-lloyd.co.uk`.** Only worth it for what CSDL lacks: which
   parameters are genuinely required, enum labels, and worked examples. Complements metadata;
   never replaces it.

Recommended: (1) for the client, plus a small scrape or manual capture for the
required/optional and enum-label semantics.

## 7. Generating the catalogue

`pkgs/morgan-lloyd/scripts/introspect_api.py` fetches `$metadata` and writes `catalogue.json`
— every `cam_*` action with its parameter names, types and return type — then reports coverage
against the client:

    moon run morgan-lloyd:introspect                       # live
    uv run python scripts/introspect_api.py --metadata <saved.xml>

Current state: **86 actions, 452 parameters.** 51 implemented, 37 not. Critically, the
"implemented but absent from the org" list is **empty**, so no client action is a typo or a
removed operation.

Most of the 37 are internal batch/utility operations that we have no reason to call
(`cam_1399001GetNextWorkingDay`, `cam_ProcessBankInterestBatch`,
`cam_MergeSharePointDocumentLocation`). Worth a look for our purposes:
`cam_amenddrawdown`, `cam_createbankstatement`, `cam_createpolicyvaluationsfromcsv`,
`cam_Createfactfind`, `cam_bulkupdatepolicies`.

### The docs-site enricher

`scripts/enrich_catalogue.py` scrapes the published docs into `docs-catalogue.json` — the
semantic half that CSDL structurally cannot express (§6). The site is Slate-generated and
regular enough to read mechanically:

    moon run morgan-lloyd:scrape-docs                      # live
    uv run python scripts/enrich_catalogue.py --html <saved.html>

Current state: **65 operations, 397 documented fields, 132 marked required, 59 carrying
option-set labels.** 48 distinct `cam_*` actions are documented; the org has 86.

Per field it captures `required` (plus `required_note`, because the docs qualify it — "No\*"
means _required only when creating a joint liability_), the description, and the option set as
both `enum` (label → value) and `enum_values` (every allowed value). Both are needed: the docs
label some values and not others — `schemeType` names 16 "ML SIPP" and leaves 17 anonymous, and
`sippType` lists **fourteen** bare integers with no labels at all — so keeping only the labelled
ones silently narrows what the API accepts. Footnotes under a Fields table are kept as `notes`;
they carry the marker legend ("fields marked with `**` will not update if 0 or null is
provided") and the conditional-required rules.

Three things the scrape got wrong before it got them right, all worth not relearning:

- **Operation titles are not action names, and cannot be derived from them.** The section titled
  "Create Investment" posts to `cam_createwrapper`. The action is read from the HTTP Request
  block; guessing from the heading invents `cam_createinvestment`, which does not exist.
- **`cam_`-prefixed URLs are not all actions.** `GET /cam_nominationofbeneficiaries` and
  `PATCH /cam_contributions(GUID)` are OData CRUD against entity _sets_. Conflating them with
  actions made four tables look like actions that 404.
- **Almost every section opens with the same blockquote** ("The above command returns HTTP
  structured like this:"). Taken as the summary, all 65 operations describe themselves
  identically. Only **one** operation in the whole document has real prose — these docs are
  tables and examples, and the spec is honest about that rather than filling the gap with
  boilerplate.

### The merged spec

`scripts/build_openapi.py` merges both into `openapi.json` (OpenAPI 3.1, `info.version` = the
docs' own Change Log release, currently 1.57). Importable into Apidog/Postman; the input for any
future generated client.

    moon run morgan-lloyd:build-spec

**CSDL wins on names and types; the docs win on semantics.** Where the docs miscase a parameter
the spec emits the org's spelling and keeps the docs' description and required flag — matching
case-insensitively so a typo costs the spelling, not the meaning. Option-set labels ride along
as `x-enum-labels`, because `809130016` on its own means nothing.

Three deliberate choices: undocumented actions are still emitted (marked `x-undocumented`) so
the spec does not misrepresent the surface as smaller than it is; the eight **bound** actions
take a `{target}` path parameter rather than a guessed entity set, because CSDL gives the
binding entity _type_ (`contact`) and the URL needs the _set_ (`contacts`); and the CTC
illustration engine is included despite being another host, with its body typed `text/xml` —
`application/xml` gets an HTML error page **with HTTP 200**.

The merge is pure, so `morgan-lloyd:test` asserts the checked-in `openapi.json` is exactly what
its two inputs produce. Refreshing either input needs network (and, for `$metadata`,
credentials), so those two tasks are `runInCI: false`.

### What the drift report found

43 of the 48 documented actions agree exactly with `$metadata`. The rest:

| Action                              | Disagreement                                                                                                                                                                      |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cam_createupdatepersonalasset`     | Docs say `cam_assetType` / `cam_wrapperApplication`; the org wants them **lower-case**. OData parameter names are case-sensitive, so the documented spelling is silently dropped. |
| `cam_createclientfactfind`          | Docs say `cam_risklevel1`; the org has `cam_risklevel`.                                                                                                                           |
| `cam_createtransfer`                | `cam_policyholder` exists but is undocumented.                                                                                                                                    |
| `cam_updatecontributionapplication` | `cam_contributionid` exists but is undocumented.                                                                                                                                  |
| `cam_updatemvpprofile`              | Docs say `cam_address1_line3`; the org has `cam_address1_country`.                                                                                                                |
| `cam_illustration`                  | `contact` exists but is undocumented.                                                                                                                                             |

One documented POST target, `cam_payrequest`, is not an action in `$metadata` — it is an
entity-set create, or a doc error.
