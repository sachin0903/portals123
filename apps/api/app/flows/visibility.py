"""Step visibility — evaluate an authored condition against a case context (pure, unit-tested).

A flow step may carry a ``visibility`` object so it only applies to some clients (e.g. a
"Suitability" step only for ``service_type = advised``). The engine evaluates it at instantiate
against a context built from the subject contact + the case's data; a step whose condition is not
met is created as ``skipped`` (it never blocks completion). Shape (mirrors the old app's fields
engine): a single ``{field, operator, value}`` or a group
``{logic: 'and'|'or', conditions: [{field, operator, value}, ...]}``. Unknown/absent ⇒ visible.

**This module is now a call site, not an implementation.** v3 asks the same authored grammar at
three places — trigger entry conditions, runtime branch nodes, and this plan-time visibility — and
they are deliberately ONE evaluator (``app/flows/conditions.py``, pinned to the shared
flow-contract.json) rather than three dialects an author would have to learn separately. The
semantics this module has always had are preserved there byte-faithfully, including "unknown
operator ⇒ visible" and the type-safe ``contains``; v3 adds wildcard/numeric/temporal operators and
arbitrarily nested groups, which a step's visibility gets for free. The name stays because that is
what the planner is asking about.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.flows import conditions


def evaluate(visibility: Any, ctx: Mapping[str, Any]) -> bool:
    """True if a step with this ``visibility`` should apply, given the context (absent ⇒ True)."""
    return conditions.evaluate(visibility, ctx)
