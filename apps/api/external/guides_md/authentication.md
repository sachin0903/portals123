# Authentication

Authenticate with an API key on every request:

```
Authorization: Bearer <key>
```

Live keys use the prefix `live_`. Test keys use `test_`. The secret is shown once, when the key is created. A key belongs to one firm.

While this API is serving stub data, the only accepted token is `test_dummy`, and every response includes `X-Graphene-Stub: true`.

A revoked key stops working in the process that revoked it immediately, and on any other process within a minute.
