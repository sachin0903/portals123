"""Everything baton raises, in three families.

* Raised **by a leaf body** to say what happened to its attempt: ``Retry``, ``NotReady``,
  ``Permanent``, ``Skip``. Each may carry the vendor exchange so the attempt row records it.
* Raised **into a composite body** by the engine: ``ChildFailed``, ``Rejected``,
  ``Conflicted``, ``ProgrammingError``. A body may catch ``ChildFailed`` to take another path.
* Raised **by the Engine API** to the caller: ``NotFound``, ``ApprovalError``, ``SubjectBusy``,
  ``InvalidTransition``, ``LeaseBusy``.

The three ``BaseException`` signals at the bottom are control flow between the engine and a
body it is replaying. They are not ``Exception`` so that a body's ``except Exception`` cannot
swallow a suspension.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from baton.states import State


class BatonError(Exception):
    """Base of everything this package raises."""


class _Evidenced(BatonError):
    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        request: Any = None,
        response: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request = request
        self.response = response


class Retry(_Evidenced):
    """A transient failure. Counts against ``retries``. ``delay`` overrides the policy's."""

    def __init__(
        self,
        message: str = "",
        *,
        delay: float | None = None,
        status_code: int | None = None,
        request: Any = None,
        response: Any = None,
    ) -> None:
        super().__init__(message, status_code=status_code, request=request, response=response)
        self.delay = delay


class NotReady(_Evidenced):
    """A durable wait: the world is not ready yet. Does not count against ``retries``; bounded
    by ``wait_timeout_seconds`` measured from the first wait of this run."""

    def __init__(
        self,
        message: str = "",
        *,
        delay: float = 5.0,
        status_code: int | None = None,
        request: Any = None,
        response: Any = None,
    ) -> None:
        super().__init__(message, status_code=status_code, request=request, response=response)
        self.delay = delay


Wait = NotReady


class Permanent(_Evidenced):
    """Another try will not help. The run fails now."""


class Skip(BatonError):
    """Nothing to do. The run completes as ``Skipped`` with ``output`` (default ``None``)."""

    def __init__(self, reason: str = "", *, output: Any = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.output = output


class ChildFailed(BatonError):
    """A child run failed and the call site said ``on_failure="fail"``."""

    def __init__(self, key: str, child_uid: str, message: str | None) -> None:
        super().__init__(message or f"{key} failed")
        self.key = key
        self.child_uid = child_uid
        self.cause = message


class Rejected(BatonError):
    """A gate was rejected. The run that raised it ends ``cancelled/Rejected``."""

    def __init__(self, key: str, note: str | None = None) -> None:
        super().__init__(note or f"{key} rejected")
        self.key = key
        self.note = note


class Conflicted(BatonError):
    """The replay diverged from what the store holds. Nothing further is executed."""


class ProgrammingError(BatonError):
    """A body mixed its own work (``ctx.session``) with child calls, or misused the API."""


class NotFound(BatonError):
    pass


class ApprovalError(BatonError):
    def __init__(self, message: str, *, status: int) -> None:
        super().__init__(message)
        self.status = status


class SubjectBusy(BatonError):
    """``one_open_per_subject``: another open run already holds this subject."""


class InvalidTransition(BatonError):
    pass


class LeaseBusy(BatonError):
    pass


class Suspend(BaseException):
    """Unwind the body; the run rests in ``state`` until ``wake_at`` (``None``: until woken)."""

    def __init__(self, state: State, *, wake_at: datetime | None = None) -> None:
        super().__init__(state.name)
        self.state = state
        self.wake_at = wake_at


class BudgetExhausted(BaseException):
    """The inline budget ran out before the next piece of work; a worker continues."""


class LeaseLost(BaseException):
    """Another holder took the run. Write nothing more."""
