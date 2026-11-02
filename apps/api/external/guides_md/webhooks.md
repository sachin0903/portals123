# Webhooks

`PUT /webhook` registers one HTTPS URL for the firm. `GET /webhook` reads it. `DELETE /webhook` removes it, and with it any delivery still waiting.

```
{ "url": "https://example.com/graphene" }
```

The URL must use HTTPS and must not contain a user name or password. Its host must resolve only to public addresses. A URL that is not HTTPS is `400` `invalid_body`. A host that does not resolve, or that resolves to a private, loopback, link-local, or other non-public address, is `422` `invalid_webhook_url`. The same rules are checked again before every delivery, and a delivery connects to the address it checked.

Each `PUT` returns a new signing secret. It is shown only in that response. Setting the URL again replaces the secret, so update your verification at the same time.

## Deliveries

When the status of a check changes, Graphene sends `POST` to the URL with this JSON body:

```
{"event":"check.updated","uid":"c2Gn7rQa4HjL","kind":"identity","status":"clear"}
```

`uid` is the check's id, `kind` is `identity` or `aml`, and `status` is `pending`, `clear`, `refer`, or `rejected`. The request has `Content-Type: application/json`, `X-Graphene-Signature`, and `X-Graphene-Delivery`, the id of the delivery. A retry sends the same `X-Graphene-Delivery` again.

Any `2xx` answer accepts the delivery. The response body is not read. Graphene waits at most 10 seconds for an answer and does not follow redirects: a `3xx` is a failed attempt.

## Retries

A failed attempt is retried after 1 minute, 5 minutes, 30 minutes, 2 hours, 6 hours, and 24 hours. When the seventh attempt fails, the delivery stops. Graphene can send a stopped delivery again on request.

A delivery can arrive more than once, and two changes to one check can arrive out of order. Use `X-Graphene-Delivery` to drop a repeat. `GET /checks/{uid}` is the source of truth.

## Verifying the signature

The header is `X-Graphene-Signature: t=<unix>,v1=<hex>`. `t` is the Unix time the delivery was signed. `v1` is the hex HMAC-SHA256 of `{t}.{body}`, keyed with the signing secret, where `body` is the raw request body. Verify the raw bytes before you parse the JSON, compare in constant time, and refuse a `t` that is more than five minutes from your clock.

```python
import hashlib
import hmac
import time


def verify(secret: str, body: bytes, header: str, tolerance: int = 300) -> bool:
    parts = dict(item.split("=", 1) for item in header.split(","))
    timestamp = int(parts["t"])
    if abs(time.time() - timestamp) > tolerance:
        return False
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return hmac.compare_digest(mac.hexdigest(), parts["v1"])
```

```javascript
const crypto = require("node:crypto");

function verify(secret, body, header, tolerance = 300) {
  const parts = Object.fromEntries(
    header.split(",").map((item) => item.split("=", 2)),
  );
  const timestamp = Number(parts.t);
  if (Math.abs(Date.now() / 1000 - timestamp) > tolerance) return false;
  const mac = crypto
    .createHmac("sha256", secret)
    .update(`${timestamp}.`)
    .update(body)
    .digest("hex");
  const given = String(parts.v1 ?? "");
  return (
    given.length === mac.length &&
    crypto.timingSafeEqual(Buffer.from(mac), Buffer.from(given))
  );
}
```

In the JavaScript example, `body` is the raw request body as a `Buffer`.
