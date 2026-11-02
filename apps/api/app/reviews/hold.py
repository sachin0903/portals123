"""The designated-endpoint seam: hold if the firm has four-eye on, else let the caller apply."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.reviews.service import HoldResult, ReviewError, maybe_hold


def pending_response(result: HoldResult) -> JSONResponse:
    return JSONResponse(status_code=202, content=result.body)


def http_error(exc: ReviewError) -> dict[str, Any]:
    """Shape a ReviewError as a FastAPI HTTPException detail.

    Field-scoped 422s keep the field name so the form can put the message on the
    justification box rather than a toast.
    """
    if exc.field:
        return {"detail": exc.detail, "field": exc.field}
    return {"detail": exc.detail}


def raise_review(exc: ReviewError) -> None:
    raise HTTPException(status_code=exc.status, detail=http_error(exc)) from exc


def idempotency_key(
    action_key: str, subject_type: str, subject_id: str, payload: dict[str, Any]
) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[
        :24
    ]
    return f"{action_key}:{subject_type}:{subject_id}:{digest}"


__all__ = ["HoldResult", "ReviewError", "maybe_hold", "pending_response", "http_error"]
