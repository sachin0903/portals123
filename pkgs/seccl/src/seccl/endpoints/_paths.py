"""Seccl's two URL conventions, in one place because mixing them up is silent.

A **create** POSTs to the bare collection — ``POST /client``, ``POST /account``,
``POST /model`` — and the firm is named in the BODY as ``firmId``. A **read or update** puts
the firm in the PATH — ``GET /client/{firm}/{id}``, ``PATCH /account/{firm}/{id}``.

That asymmetry is easy to get wrong in a way nothing catches locally: posting to
``/client/{firm}`` is a 404 that reads like a routing problem, and reading ``/client`` without
the firm segment returns another firm's shape of error rather than a clear refusal. So the
paths are built here and the two forms are named differently.

`{firm}` is always the client's configured firm id — `SecclSettings.firm_id`. It is not a
parameter on the endpoint methods, because a firm mismatch between the token and the path is
not something a caller should be able to express.
"""

from __future__ import annotations


def collection(resource: str) -> str:
    """The create path: no firm segment (the firm goes in the body)."""
    return f"/{resource}"


def scoped(resource: str, firm_id: str) -> str:
    """A firm-scoped list path: ``/{resource}/{firm}``."""
    return f"/{resource}/{firm_id}"


def record(resource: str, firm_id: str, identifier: str) -> str:
    """A firm-scoped record path: ``/{resource}/{firm}/{id}``."""
    return f"/{resource}/{firm_id}/{identifier}"
