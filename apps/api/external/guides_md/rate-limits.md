# Rate limits

Each API key may make 10 requests per second.

Unknown bearers share a separate limit and are refused before the key is read. A key this service has already accepted is not part of that limit.

A request over the limit is `429` with code `rate_limited`.
