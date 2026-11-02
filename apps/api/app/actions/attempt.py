"""The result of one provider call, and the error that stops another try."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

Handler = Callable[..., Awaitable["Attempt"]]

SAFE_ATTEMPTS = 3


class OperationError(Exception):
    """The operation cannot be sent. ``permanent`` means another try will not help."""

    def __init__(
        self,
        message: str,
        *,
        permanent: bool = False,
        status_code: int | None = None,
        response: Any = None,
        method: str | None = None,
        url: str | None = None,
        request: Any = None,
    ):
        super().__init__(message)
        self.permanent = permanent
        self.status_code = status_code
        # The provider call's evidence, recorded on the attempt row even when the call FAILED, so a
        # refused send shows its method/url/request/response in the run detail, not only on success.
        # The full response body also lives in audit.api_log.
        self.response = response
        self.method = method
        self.url = url
        self.request = request


@dataclass(slots=True)
class Attempt:
    ok: bool
    status_code: int | None = None
    method: str | None = None
    url: str | None = None
    request: Any = None
    response: Any = None
    error: str | None = None
    duration_ms: int | None = None
    skipped: bool = False
    # The handler's exception, kept so a door can map it to an HTTP status after the attempt
    # row is written. The worker does not re-raise it.
    raised: BaseException | None = None
