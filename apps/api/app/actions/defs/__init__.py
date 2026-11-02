"""The domain actions, as baton actions, registered onto baton's default registry on import.

Every catalogue action (apps/api/app/actions/catalogue.py) lives here as a leaf or a composite,
one module per domain; importing a module registers its ``@action``s. The provider leaves wrap
the handlers in app.integrations.*; the firm-facing composites resolve the provider from the book
and call them. ``placeholders`` registers the four catalogue entries that have no implementation
yet, so the generic Run button refuses them with a clear message.
"""

from __future__ import annotations

from app.actions.defs import (  # noqa: F401 - registers actions
    accounts,
    book,
    clients,
    money,
    morgan_lloyd,
    placeholders,
    resolve,
    sei,
    setup,
)

__all__ = [
    "accounts",
    "book",
    "clients",
    "money",
    "morgan_lloyd",
    "placeholders",
    "resolve",
    "sei",
    "setup",
]
