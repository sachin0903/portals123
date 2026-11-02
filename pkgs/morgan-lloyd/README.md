# morgan-lloyd — CRM API client

Async Python client for the Morgan Lloyd (Clifton Asset Management) API —
a Microsoft Dynamics 365 instance exposed through custom actions (`cam_*`)
plus standard OData entity reads. Docs: https://dev.api.morgan-lloyd.co.uk/.

## Shape

- `MorganLloydClient` — async context manager composing one mixin per docs
  section (`endpoints/contacts.py`, `factfinds.py`, `schemes.py`,
  `transfers.py`, `benefits.py`). Methods take/return plain dicts keyed by the
  CRM's field names; identifiers are explicit parameters.
- `morgan_lloyd.enums` — every documented Dynamics option set, transcribed
  verbatim from the docs (the `8091300xx` ranges, small-int codes, string
  flags). Multiselect fields are sent as comma-separated strings — build them
  with `enums.multiselect(...)`.
- Transport (`_base.py`): Azure AD (v1) client-credentials grant against
  `login.microsoftonline.com` with the Dynamics organisation URL as the
  `resource`; token refreshed before expiry, one in-attempt re-auth on 401,
  429/5xx/transport retries with exponential backoff, bounded concurrency.
- Custom-action responses carry an envelope (`responseKey`/`message`, flat or
  under `response`); `responseKey=1` raises `MorganLloydActionError` even
  though the HTTP status is 200.

## Config

All vars `MORGAN_LLOYD_*` (see `MorganLloydSettings`); credentials are the
tenant/client/secret trio issued by Morgan Lloyd (crmsupport@clifton-asset.co.uk):

```
MORGAN_LLOYD_RESOURCE_URL=https://cliftonassetdev.crm11.dynamics.com
MORGAN_LLOYD_TENANT_ID=<azure directory guid>
MORGAN_LLOYD_CLIENT_ID=<registered app client id>
MORGAN_LLOYD_CLIENT_SECRET=<app secret>
```

## Gotchas (from the docs, preserved deliberately)

- Action names are case sensitive — including the mixed-case
  `cam_CreateFinancialAdviserContact`.
- Several fields are strings-in-disguise: `cam_potentiallyvulnerable` is the
  string `"True"`/`"False"`; ATR data, bulk scheme updates, policy-valuation
  fund splits and contribution uploads are JSON **strings** (the methods
  serialise for you); vulnerability is a comma-separated multiselect string.
- `PATCH` updates address lookups with the `@odata.bind` suffix and entity
  paths, not bare GUIDs.
- Documented misspellings are real field names (`cam_uncrystalllisedtransfervalue`,
  `crystalisationFee`) — do not "fix" them.
- The same field name can carry different option sets on different endpoints
  (`cam_contributiontype`) — the enums module keeps them as separate classes.

`moon run morgan-lloyd:lint|format|typecheck|test` runs the gates.
