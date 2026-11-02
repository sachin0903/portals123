# Errors

Errors are JSON with `code` and `message`.

- `unknown_version` — `X-Version` is not a version this API serves.
- `unauthorized` — the bearer token is missing, unknown, expired, or revoked.
- `documents_required` — an identity check needs a selfie and a passport, driving licence, or identity document already stored for the client.
- `address_country_required` — an AML check needs an address country.
- `file_too_large` — the decoded document is larger than 50 MB.
- `storage_unavailable` — the document could not be stored. Nothing was kept; send it again later.
- `invalid_webhook_url` — the webhook host does not resolve, or resolves to an address that is not public.
- `not_found` — that client or check is not in this firm.
- `rate_limited` — the key has used its 10 requests per second.

A body that fails validation is `400`. Unknown fields are rejected rather than ignored.
