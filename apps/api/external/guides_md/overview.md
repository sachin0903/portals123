# Overview

The Graphene API lets a firm create a client, run an identity check and an AML check, and record the custodian id once the firm has opened that client at Seccl or SEI.

Requests are JSON. Responses are JSON. A missing `X-Version` header uses the latest version, and the response echoes the version that was applied.

The resources are clients, documents, checks, custodian links, and a webhook. `GET /health` is liveness and does not need a key.
