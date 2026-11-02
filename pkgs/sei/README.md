# sei — SEI Wealth Platform (SWP) API client

Async Python client for [SEI](https://www.seic.com)'s Wealth Platform, the custodian behind the
SIPP book. Covers **only the services Graphene calls** — today, the cash-receipt service used to
lodge a payment expectation against a client's account.

## Why this is hand-written, not generated

`pkgs/seccl` merges a Postman collection and generates its models; that approach does not
transfer. SEI documents SWP as **~200 separate Swagger files with no combined spec** (they are
vendored in the monitoring repo under `apps/api/app/resources/sei/openapi/`), each describing one
operation, with overlapping and sometimes contradicting component names. Generating from 200
specs to reach three endpoints would produce far more surface than it removes.

So the payloads are transcribed by hand and **pinned by tests**: `tests/test_cash_receipts.py`
validates every payload the models can build against the spec's own JSON Schema, and checks in
both directions that no field was invented and no required field missed. The one spec we depend
on is vendored here (`spec/`) so CI never reaches for a sibling repo.

## Shape

- `SeiClient` — async context manager composing one mixin per SWP service
  (`endpoints/cash_receipts.py`). Entering the context validates configuration and loads the mTLS
  identity, so a missing credential fails before any network call.
- `sei.models` — typed request/response payloads for the cash-receipt service.
- `sei.enums` — SWP's code sets, transcribed verbatim from the spec's field descriptions.
- `sei.settings` — per-environment credentials (`i10` / `i31` / `p04`).

## Auth — three credentials, all required

| Credential           | What it is                                        | Where it goes                      |
| -------------------- | ------------------------------------------------- | ---------------------------------- |
| **mTLS certificate** | A PKCS#12 bundle SEI issues; the machine identity | TLS client cert                    |
| **OAuth token**      | Password grant at `/oauthsecurity/access_token`   | `Authorization: Bearer`            |
| **AppKey**           | App-gateway key                                   | `AppKey` header on _every_ request |

The token call is unusual and worth knowing: `AppKey` and `grant_type` go in as **headers**, the
app pair is **HTTP Basic**, and only the username/password go in the form body. That is what the
SWP spec asks for, and it is pinned by `test_token_request_matches_seis_spec`.

## Config

Credentials are **per environment** with a shared fallback, so one `.env` serves all of them
(the same convention the monitoring repo's probes use):

```
SEI_{ENV}_{FIELD}      e.g. SEI_I31_APPKEY      ← tried first
SEI_{FIELD}            e.g. SEI_PFX_PASSWORD    ← fallback
```

```
SEI_PFX_BASE64=…                    # `base64 -w0 sei.pfx` — the only form; see below
SEI_PFX_PASSWORD=…                  # usually shared: the cert is the machine identity
SEI_I31_OAUTH_USERNAME=i31pographeneapi50
SEI_I31_OAUTH_PASSWORD=…
SEI_I31_APPKEY=…
SEI_I31_APPSECRET=…
```

`SEI_PFX_BASE64` is the **only** certificate form. A file path was accepted until Aug 2026 and is
gone: a path needs a file inside the image or a bind mount, and a stack configured from one
environment list has nowhere to put either — so nothing deployable used it, while carrying both
meant two precedence rules that had already drifted (`configured` asked for the path,
`ssl_context` preferred the base64, and a container supplying only base64 was refused naming the
variable it could not set). A newline-wrapped value — what a deployment UI's textarea gives you —
is fine. For local work, `base64 -w0 sei.pfx` into your `.env`.

## Gotchas (from the spec, preserved deliberately)

- **SEI allowlists by source IP.** A perfectly configured client still gets 401/403 from anywhere
  but the allowlisted host, so it cannot be exercised from a laptop. That surfaces as
  `SeiAuthError` with an explanatory message rather than looking like an outage.
- **Every scalar is a string**, including amounts and the numeric code sets — SEI types the whole
  payload `string` and rejects a JSON number. Use `sei.money()`, which quantizes rather than
  calling `Decimal.normalize()` (`Decimal("60").normalize()` renders as `6E+1`).
- **Dates are `YYYY-MM-DD`.** The field descriptions say `[YYYY/MM/DD]`; every example in the
  spec, and the service itself, uses hyphens.
- **`descriptionTypeCode` / `treatmentTypeCode` are firm-configured reference data**, not fixed
  codes — SEI provisions them per firm and validates the pair against the transaction type _and_
  the caller's role. No lookup endpoint exists in the published spec set, so they must be supplied
  as configuration. They are the pair that actually classifies a contribution at SEI's end.
- **`firmId` is required in practice.** The spec says "required if caller is at the Processing
  Organization level" — Graphene's SEI users are PO-level (`i31pographeneapi50`), so omitting it
  returns error 1001.
- **There is no ACR flag.** An Anticipated Cash Receipt is a receipt with a future `dueDate` and
  no `payerInfo`. Direct Debit is the same call _with_ a payer profile.
- **A 200 can carry warnings** (approval required, a fee override ignored). `ReceiptResult.warnings`
  must be read — 200 is not proof the receipt went in exactly as sent.
- **The spec's server is the extranet gateway** (`extranettest.api.seic.com`); we call the mTLS
  gateway (`test.mtls.api.seic.com`), which is what our certificate is issued for.

`moon run sei:lint|format|typecheck|test` runs the gates.
