# Morgan Lloyd — API Reference

CTC - https://cam.ctc.uk.com/Pages/Remote.aspx?wci=RunQuote&nodb=true

Morgan Lloyd's back office is **Dynamics 365 / Dataverse**, exposed as an **OData v4 REST API**.

- **Base URL (dev):** `https://cliftonassetdev.crm11.dynamics.com/api/data/v9.1`
- **Vendor docs (dev):** `https://dev.api.morgan-lloyd.co.uk/` (single-page; `#authentication` section)
- **Credentials:** Application ID + Tenant ID in `Morgan Lloyd API Details- Graphene.docx`; the **Secret Key is delivered separately/encrypted** — never commit it. Store all three in env / secret manager (see [operations.md](./operations.md)).

## Authentication (Azure AD S2S OAuth2, client-credentials)

```
POST https://login.microsoftonline.com/{tenant_id}/oauth2/token
  grant_type=client_credentials
  client_id={application_id}
  client_secret={secret}
  resource=https://cliftonassetdev.crm11.dynamics.com
→ { access_token, token_type: "Bearer", expires_in }
```

Every request carries `Authorization: Bearer {access_token}`. Cache the token and refresh before `expires_in`. Verify connectivity with `GET /api/data/v9.1/WhoAmI` (returns `UserId`, `BusinessUnitId`, `OrganizationId`).

## Conventions

- Custom **bound/unbound actions** are invoked as `POST {base}/cam_<operation>` with a JSON body. Entity **reads/updates** use OData (`GET /contacts(<guid>)`, `PATCH /contacts(<guid>)`).
- Lookups are set with the `@odata.bind` suffix (e.g. `"cam_schemeholder@odata.bind": "/contacts(<guid>)"`).
- Responses are OData JSON (`@odata.context`, `@odata.etag`, `value[]`) and most actions return a `response` object with `responseKey` + `message`. Updates return `204 No Content`.

## Fixed reference values (from API Details docx)

| Field        | Endpoint          | Value                                  |
| ------------ | ----------------- | -------------------------------------- |
| `schemeType` | Create Scheme     | `16`                                   |
| `sippType`   | Create Scheme     | `809130016`                            |
| `provider`   | Create Investment | `6ac39921-d8b7-f011-bbd3-7ced8d6b85ff` |

## Operation catalogue

### Auth / health

| Op                              | Purpose             |
| ------------------------------- | ------------------- |
| `GET .../{tenant}/oauth2/token` | Obtain access token |
| `GET /WhoAmI`                   | Verify token        |

### Contacts

| Op                          | Purpose                       | Key fields                                                                                        |
| --------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------- |
| `cam_createcontact`         | Create contact                | firstname, lastname, emailaddress1, cam_ninumber, cam_title, gendercode, cam_nationality, address |
| `cam_getadvisercontacts`    | Contacts for an adviser       | adviserEmailAddress                                                                               |
| `cam_getcontactidschemeref` | Resolve contactId + schemeRef | policyNo, platformInvestorNo                                                                      |
| `GET/PATCH /contacts(<id>)` | Read / update contact         | —                                                                                                 |
| `cam_submitatr`             | Attitude-to-Risk record       | clientId, riskProfile, dateGenerated, atrData                                                     |

### Factfind (likely out of scope for SIPP onboarding; available)

`cam_getclientfactfind`, `cam_createclientfactfind`, `cam_createupdatesoftfacts`, `cam_getemployeraccounts`, `cam_createupdateaccount`, `cam_get/createupdate{personalasset,liability,relationship}`, `cam_createnote`, `cam_createtask`, `cam_getallproviders`.

### Schemes

| Op                         | Purpose                                               | Key fields                                                        |
| -------------------------- | ----------------------------------------------------- | ----------------------------------------------------------------- |
| `cam_createscheme`         | Create SIPP scheme                                    | cam_schemeholder, cam_schemereference, sippType, schemeType, fees |
| `cam_updatescheme` (PATCH) | Update scheme                                         | cam_schemeid + fields                                             |
| `cam_bulkupdateschemes`    | Bulk update                                           | array                                                             |
| `cam_getschemebyschemref`  | Scheme detail incl. crystallised/uncrystallised split | cam_schemereference                                               |
| `cam_getschemerevidate`    | Review date                                           | cam_schemereference                                               |

### Beneficiaries

`cam_createbeneficiary` (cam_scheme, cam_nominatedbeneficiary, cam_beneficiarytype) · `cam_getbeneficiariesbyschemeref` · `GET/PATCH /cam_beneficiaries(<id>)`.

### Transfers

`cam_createtransfer` (cam_policy, cam_provider, transferamount, provider address, notes) · `cam_gettransfer` · `cam_gettransferbypolicyno` · `cam_getalltransfersbysceme` · `cam_updatetransfer` (PATCH).

### Pension Contributions

`cam_createpensioncontribution` (cam_scheme, contributiontype, contributionamount) · `cam_getpensioncontributionapplications` · `cam_getreceivedcontributions` · `cam_updatepensioncontribution` (PATCH) · `cam_updatecontributionpension_status` (PATCH) · `cam_updatecontributionapplication` · `cam_uploadpensioncontributions` (bulk).

### Investments & Valuations

`cam_createinvestment` (cam_scheme, provider, investmentdetails/amount) · `cam_createpolicyvaluation` (cam_policy, valuationdate, valuationamount + crystallised/uncrystallised split).

### Benefits / Drawdown

| Op                                      | Purpose                      | Notes                                                                  |
| --------------------------------------- | ---------------------------- | ---------------------------------------------------------------------- |
| `cam_retrievememberdetails`             | Member data                  | withdrawals, tfcUsed, flatFee                                          |
| `cam_createbenefitapplication`          | Start benefit application    | returns `ssoLink` → DCube                                              |
| `cam_updatebenefitillustration` (PATCH) | Update illustration template | —                                                                      |
| `cam_drawdownsetup`                     | Configure drawdown           | drawdownOption, investmentPathway, tfcUsed, guidance/advice attendance |
| `cam_getbenefitevents`                  | Benefit events for scheme    | eventStatus, excludeAdHoc, nextPaymentDate                             |
| `cam_canceldrawdown`                    | Cancel                       | by benefitApplicationId OR policyNumber+withdrawalType+withdrawalId    |
| `cam_payrequest`                        | Process pay request          | paymentamount, paymentdate                                             |

### Documents / Illustrations / SSO / Messaging

`cam_submitdocument` (cam_contact, documentFolder, documentcontent, documentreference, productType) · `cam_submitillustration` (cam_scheme, illustrationdata, illustrationType) · `cam_validatesso` (ssotoken, cam_contact) · `cam_createthread` / `cam_createmessage` / `cam_updatemessage`.

### Bank / financial

`cam_getbanktransactions` (cam_schemereference → transactions with inbound/outbound direction).

> Field-level request/response details should be confirmed against the live vendor docs at `https://dev.api.morgan-lloyd.co.uk/` during implementation; this catalogue is the operation map.
