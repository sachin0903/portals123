"""The generic, config-driven flow engine.

A *flow* is a firm-authored, versioned process definition (tier-scoped steps + declarative
milestones); an *execution* runs a published flow over an application (its people + accounts),
fanning out into per-person *profile* cases and per-account *account* cases. The engine derives
each case's + the execution's state from its steps; onboarding is simply the first published
flow — there is no onboarding-specific code beyond the seeded definition and the step handlers.

- ``definitions`` — validate/normalise the authored definition JSON (strict allowlist).
- ``planning`` — fan a tier-scoped definition + application graph into cases + steps (the KYC gate).
- ``orchestrator`` — the DB spine: plan → persist → consume events → derive → project.
- ``derivation``/``projection`` — pure state derivation + the legacy onboarding-label projection.
- ``step_handlers`` — the module adapters (fill_form, verify_identity, accept_terms, review, …).
- ``onboarding_seed``/``onboarding_terms`` — the seeded onboarding flow, forms and terms.
- ``advice_seed`` — the fact-find journey written for one firm (fact find, scored risk profile,
  adviser assessment). Reference only: applied by ``scripts/seed_advice_journey.py``, not at
  provisioning.
- ``portability`` — a flow as a file (with the forms and templates it names) and back again.
"""

# Import for side-effect: registers every concrete StepHandler in the handler registry so the
# orchestrator can resolve step types. Kept last to avoid import cycles.
from app.flows import step_handlers as _step_handlers  # noqa: E402, F401
