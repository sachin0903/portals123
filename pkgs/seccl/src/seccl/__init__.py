"""Seccl API client and typed payloads.

Two halves, and they have different provenance:

* **The client** (`seccl.client`, `seccl.endpoints`) — the transport, auth and endpoint methods.
  Written against the live API and validated against it; `services/seccl` has run it over a
  whole book, and its notes on Seccl's unstable page-boundary ordering are the load-bearing
  part.
* **The payload models** (`seccl.models`) — **inferred from examples**, not from a schema.
  Seccl publishes no OpenAPI document and no JSON Schema: `spec/seccl.postman_collection.json`
  is the vendor's whole documentation, and `scripts/build_schemas.py` merges its 205 example
  bodies into one JSON Schema per resource for `datamodel-code-generator`. "Required" there
  means "present in every example", which is decent evidence and not a guarantee — which is
  why `app/platform_contracts.py` does not enforce requiredness for Seccl.

Where the two disagree, the client wins.
"""

from seccl._base import BaseClient, SecclApiError
from seccl.client import SecclClient
from seccl.settings import SecclSettings

__all__ = ["BaseClient", "SecclApiError", "SecclClient", "SecclSettings", "models"]
