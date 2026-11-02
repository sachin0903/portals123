# Versioning

Send `X-Version: v1`.

URLs are not versioned. If the header is missing, the request uses the latest version. The response echoes the version that was applied. An unknown value is rejected with `400` and `unknown_version`.

A later version is added when behaviour changes. Older versions keep working until they are withdrawn.
