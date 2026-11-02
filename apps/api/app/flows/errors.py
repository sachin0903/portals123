"""Errors the engine raises when a caller asks for a transition its own invariants forbid.

``StepTransitionRefused`` deliberately subclasses ``HTTPException``. The invariant it carries lives
with the **transition** (``orchestrator.submit_step``), not with one router, precisely because the
same transition is reachable from several routes — the staff form-fulfilment endpoint, the staff
``/action`` endpoint and the client document-fulfilment endpoints all end up in the same handler.
Making it an ``HTTPException`` means every one of those routes answers with the same honest 422 (and
rolls its transaction back, because the request-scoped ``session.begin()`` unwinds on any exception)
rather than one route enforcing the rule and the others 500ing or silently succeeding.

Same idiom as the authorisation guards in ``app/auth.py``/``app/db.py``: enforcement code raises the
HTTP answer directly instead of every call site remembering to translate it.
"""

from __future__ import annotations

from fastapi import HTTPException, status


class StepTransitionRefused(HTTPException):
    """A step was asked to transition in a way its evidence does not support.

    Carries the human sentence straight through to the caller (adviser or client), so the refusal is
    never a bare 500 or — worse — an ``{"ok": true}`` over a transition that never happened.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)
