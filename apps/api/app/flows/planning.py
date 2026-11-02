"""Planning — the pure decisions the orchestrator asks before it touches the database.

Two halves, both kept free of any DB so they are exhaustively unit-testable:

**Tiering** (the original, below) — fan a tier-scoped flow definition + an application graph into
concrete cases + steps.

**Graph runtime** (v3, at the foot of this module) — the three questions a control node makes the
release pass ask: which steps sit behind a control node and must therefore wait to be *let through*
rather than merely un-blocked; which steps an untaken branch arm has just made unreachable; and
what is reachable from a node at all. ``app/flows/graph.py`` owns the graph's shape and its
plan-time dependency compiler; these are the runtime companions, and they live here because they
are pure and the orchestrator that calls them is not.

Tiering, in detail
------------------
This is the heart of the three-tier model (docs/flows.md §2). Given:

- the authored steps (each with a ``scope`` — participant | account | application — a ``type``, and
  optional ``depends_on`` step keys + ``per_participant`` fan-out), and
- an ``ApplicationGraph`` (the participants with their contact type + account roles, and the
  accounts with their participants),

it produces the **plan**: one ``profile`` case per participant, one ``account`` case per account,
an optional ``application`` case, each with its fanned step instances, and — crucially — the
**cross-case KYC gate**: an account case lists the contact ids whose profile cases must complete
before it is released (the "profiles + KYC first, then accounts" ordering).

KYC-relevance: an *individual* in an ``owner`` / ``decision_maker`` / ``trustee`` /
``registered_contact`` role, except a child owner whose account names an adult registered contact
(the registered contact is KYC'd instead).
Children recorded as ``type='child'`` and organisations are gathered but not identity/AML-gated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.flows import graph as graph_mod
from app.flows import visibility as visibility_mod

# Account roles that make an individual subject to KYC / portal invite (legacy owner gates, plus
# the registered contact). A child owner with that adult is excluded in ``is_kyc_relevant`` even
# though ``owner`` is in this set — they are the account holder, not the person we IDV.
# ``party`` is deliberately absent: a person on a journey that opens no account is a participant
# for fact-find forms, and is not screened or invited as an account holder.
KYC_ROLES: frozenset[str] = frozenset({"owner", "decision_maker", "trustee", "registered_contact"})
#: Roles that always mean "this adult is screened / invited", even on a JISA.
_ADULT_KYC_ROLES: frozenset[str] = frozenset({"decision_maker", "trustee", "registered_contact"})
KYC_STEP_TYPES: frozenset[str] = frozenset({"verify_identity", "aml_check"})

Scope = str  # "participant" | "account" | "application"

_DEFAULT_SCOPE: dict[str, Scope] = {
    "verify_identity": "participant",
    "aml_check": "participant",
    "fill_form": "participant",
    "upload_document": "participant",
    "accept_terms": "account",
    "setup_account": "account",
    "generate_document": "application",
    "task": "application",
}


def default_scope(step_type: str) -> Scope:
    return _DEFAULT_SCOPE.get(step_type, "application")


# Product codes whose accounts count as a pension (drives the conditional pension data-gather steps,
# legacy `hasPensionProduct`/`hasSippProduct`, application-form.tsx).
_PENSION_PRODUCTS: frozenset[str] = frozenset({"SIPP", "TPPENSION"})
_SIPP_PRODUCTS: frozenset[str] = frozenset({"SIPP"})


def _is_pension(product: str | None) -> bool:
    p = (product or "").strip().upper()
    return p in _PENSION_PRODUCTS or "PENSION" in p


def _completed_age(born: date, on: date | None = None) -> int:
    today = on or date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


@dataclass(frozen=True)
class Participant:
    contact_id: int
    type: str  # 'individual' | 'organisation' | 'child' | ...
    roles: frozenset[str]  # union of this contact's roles across the application's accounts
    dob: date | None = None


@dataclass(frozen=True)
class AccountSpec:
    account_id: int
    participant_contact_ids: tuple[int, ...]
    product: str | None = None  # product code (GIA/ISA/JISA/SIPP/TPONBOND/TPOFFBOND/TPPENSION)
    custody: str | None = None  # 'onshore' | 'offshore' (falls back to the application's custody)
    has_transfer: bool = False  # an inbound transfer is attached (drives the Transfer Declaration)
    registered_contact_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class ApplicationGraph:
    participants: tuple[Participant, ...]
    accounts: tuple[AccountSpec, ...]
    custody: str | None = None  # application-level custody ('onshore' | 'offshore')

    def participant(self, contact_id: int) -> Participant | None:
        return next((p for p in self.participants if p.contact_id == contact_id), None)

    def accounts_for(self, contact_id: int) -> tuple[AccountSpec, ...]:
        return tuple(a for a in self.accounts if contact_id in a.participant_contact_ids)

    def participant_has_pension(self, contact_id: int) -> bool:
        return any(_is_pension(a.product) for a in self.accounts_for(contact_id))

    def participant_has_sipp(self, contact_id: int) -> bool:
        return any(
            (a.product or "").strip().upper() in _SIPP_PRODUCTS
            for a in self.accounts_for(contact_id)
        )

    @property
    def has_pension_product(self) -> bool:
        return any(_is_pension(a.product) for a in self.accounts)

    @property
    def has_sipp_product(self) -> bool:
        return any((a.product or "").strip().upper() in _SIPP_PRODUCTS for a in self.accounts)

    @property
    def has_transfer(self) -> bool:
        return any(a.has_transfer for a in self.accounts)

    def _app_context(self) -> dict[str, object]:
        """The application-level facts a step's ``visibility`` may key on (offshore/pension/SIPP/
        transfer), exposed under legacy ``application.*`` paths."""
        return {
            "application.custody": self.custody,
            "application.has_pension_product": self.has_pension_product,
            "application.has_sipp_product": self.has_sipp_product,
            "application.has_transfer": self.has_transfer,
        }


@dataclass(frozen=True)
class PlannedStep:
    step_key: str
    step_type: str
    title: str
    scope: Scope
    assignee: str
    required: bool
    config: dict[str, object]
    depends_on: tuple[str, ...] = ()
    participant_subject_id: int | None = None  # the fan-out instance (which person), if any


@dataclass(frozen=True)
class PlannedCase:
    kind: str  # 'profile' | 'account' | 'application'
    subject_type: str  # 'contact' | 'account' | 'group'
    subject_id: int | None
    steps: tuple[PlannedStep, ...]
    # Contact ids whose *profile* cases must complete before this case is released (cross-case KYC
    # gate). Empty ⇒ no gate (starts open).
    gate_on_contacts: tuple[int, ...] = ()


def is_kyc_relevant(p: Participant, graph: ApplicationGraph | None = None) -> bool:
    """Whether this person is IDV'd, AML-screened, and invited to the portal.

    An individual in an owner / decision-maker / trustee role is, matching legacy
    ``isIndividualForClientGates`` — except a child whose account names a registered contact, the
    adult we actually KYC. Without a graph we cannot see account composition, so an owner stays
    relevant (unit tests and any caller that has no accounts).
    """
    if p.type != "individual" or not (p.roles & KYC_ROLES):
        return False
    if p.roles & _ADULT_KYC_ROLES:
        return True
    if graph is None or "owner" not in p.roles:
        return True
    on_accounts = [a for a in graph.accounts if p.contact_id in a.participant_contact_ids]
    if not on_accounts:
        return True
    if p.dob is not None and _completed_age(p.dob) < 18:
        # The account composition, not its wrapper label, is the safety fact: a product configured
        # for a child must name the adult who is screened and invited for that account.
        return not all(a.registered_contact_ids for a in on_accounts)
    # Compatibility for graphs built before participant DOB and account-role detail were carried.
    return not all((a.product or "").strip().upper() == "JISA" for a in on_accounts)


def onboarding_role(p: Participant, graph: ApplicationGraph) -> str:
    """Classify a person for the fact-find without changing their account role.

    The JISA child owns the account while the registered contact accepts terms and completes KYC.
    Both must remain participants, but neither should receive the ordinary applicant fact-find.
    A person who also participates in a non-JISA account remains an ordinary applicant because
    that wider application still needs their full details.
    """
    on_accounts = [a for a in graph.accounts if p.contact_id in a.participant_contact_ids]
    jisa_only = bool(on_accounts) and all(
        (a.product or "").strip().upper() == "JISA" for a in on_accounts
    )
    if not jisa_only:
        return "applicant"
    if "registered_contact" in p.roles:
        return "jisa_registered_contact"
    if "owner" in p.roles:
        return "jisa_child"
    return "applicant"


def _step_scope(step: dict[str, object]) -> Scope:
    scope = step.get("scope")
    return scope if isinstance(scope, str) else default_scope(str(step.get("type")))


def _visible(step: dict[str, object], ctx: dict[str, object]) -> bool:
    """Evaluate a step's optional ``visibility`` condition against a context (absent ⇒ visible).
    Mirrors legacy dynamic-field conditions — the same operator set + AND/OR groups, so an authored
    offshore/pension/organisation branch resolves identically to the old fixed onboarding."""
    return visibility_mod.evaluate(step.get("visibility"), ctx)


def _participant_context(p: Participant, graph: ApplicationGraph) -> dict[str, object]:
    return {
        **graph._app_context(),
        "applicant.type": p.type,
        "applicant.roles": list(p.roles),
        "applicant.onboarding_role": onboarding_role(p, graph),
        # Pension questions belong to the person who owns that wrapper, not to everybody in an
        # application that happens to contain one. The Pavey family made the difference visible:
        # one SIPP produced Pension + Employer forms for the child and both parents.
        "applicant.has_pension_product": graph.participant_has_pension(p.contact_id),
        "applicant.has_sipp_product": graph.participant_has_sipp(p.contact_id),
    }


def _account_context(acc: AccountSpec, graph: ApplicationGraph) -> dict[str, object]:
    return {
        **graph._app_context(),
        "account.product": acc.product,
        # Field conditions on the Account details form use ``account.product_type``; the journey
        # gates use ``account.product``. Same fact, both names, so a mistyped authoring cannot
        # quietly light ISA terms on a GIA.
        "account.product_type": acc.product,
        "account.custody": acc.custody or graph.custody,
        "account.has_transfer": acc.has_transfer,
    }


def _step_applies_to_participant(
    step: dict[str, object], p: Participant, graph: ApplicationGraph
) -> bool:
    """A participant-scoped step applies to a participant unless (a) it is a KYC step and the person
    isn't KYC-relevant (children/orgs are gathered but not identity/AML-checked), or (b) its
    ``visibility`` condition (e.g. individual-only / organisation-only / offshore) excludes
    them."""
    if str(step.get("type")) in KYC_STEP_TYPES and not is_kyc_relevant(p, graph):
        return False
    return _visible(step, _participant_context(p, graph))


def _mk_step(step: dict[str, object], *, participant_subject_id: int | None) -> PlannedStep:
    stype = str(step.get("type"))
    depends = step.get("depends_on")
    depends_t = tuple(d for d in depends if isinstance(d, str)) if isinstance(depends, list) else ()
    cfg = step.get("config")
    return PlannedStep(
        step_key=str(step.get("id")),
        step_type=stype,
        title=str(step.get("title") or step.get("id") or ""),
        scope=_step_scope(step),
        assignee=str(step.get("assignee") or "client"),
        required=bool(step.get("required", True)),
        config=cfg if isinstance(cfg, dict) else {},
        depends_on=depends_t,
        participant_subject_id=participant_subject_id,
    )


def plan_execution(steps: list[dict[str, object]], graph: ApplicationGraph) -> list[PlannedCase]:
    """Fan the definition's steps across the graph into the concrete case + step plan."""
    by_scope: dict[Scope, list[dict[str, object]]] = {
        "participant": [],
        "account": [],
        "application": [],
    }
    for s in steps:
        by_scope.setdefault(_step_scope(s), []).append(s)

    cases: list[PlannedCase] = []

    # --- profile case per participant (participant-scoped steps that apply to them) ---
    for p in graph.participants:
        p_steps = tuple(
            _mk_step(s, participant_subject_id=p.contact_id)
            for s in by_scope["participant"]
            if _step_applies_to_participant(s, p, graph)
        )
        if p_steps:
            cases.append(
                PlannedCase(
                    kind="profile",
                    subject_type="contact",
                    subject_id=p.contact_id,
                    steps=p_steps,
                )
            )

    # A catalogue acceptance belongs to (contact, published term version), not to an account.
    # Several accounts can make the same term applicable to one client, but planning one request
    # per account asks them to accept identical legal copy repeatedly. Keep the first applicable
    # instance; dependencies in the other account cases resolve against that execution-wide
    # instance, so every affected account still waits for the one real acceptance.
    planned_catalogue_terms: set[tuple[str, int]] = set()

    # --- account case per account (account-scoped steps; per_participant fans to owners) ---
    for acc in graph.accounts:
        acc_ctx = _account_context(acc, graph)
        acc_steps: list[PlannedStep] = []
        for s in by_scope["account"]:
            if bool(s.get("per_participant")):
                # A per-owner account step (e.g. terms) fans to each owner whose *own* context
                # satisfies the step's condition — so an individual-only terms declaration skips an
                # organisation owner (mirrors the legacy individual-only terms gate).
                for cid in acc.participant_contact_ids:
                    pp = graph.participant(cid)
                    ctx = {**acc_ctx, **(_participant_context(pp, graph) if pp else {})}
                    if _visible(s, ctx):
                        config = s.get("config")
                        term_uid = (
                            str(config.get("term_uid"))
                            if str(s.get("type")) == "accept_terms"
                            and isinstance(config, dict)
                            and config.get("term_uid")
                            else None
                        )
                        if term_uid is not None:
                            acceptance = (term_uid, cid)
                            if acceptance in planned_catalogue_terms:
                                continue
                            planned_catalogue_terms.add(acceptance)
                        acc_steps.append(_mk_step(s, participant_subject_id=cid))
            elif _visible(s, acc_ctx):
                # A single account step (activity, offshore review, setup) — account-level context.
                acc_steps.append(_mk_step(s, participant_subject_id=None))
        # Cross-case gate: this account's KYC-relevant participants must finish their profile case.
        gate = tuple(
            cid
            for cid in acc.participant_contact_ids
            if (pp := graph.participant(cid)) is not None and is_kyc_relevant(pp, graph)
        )
        cases.append(
            PlannedCase(
                kind="account",
                subject_type="account",
                subject_id=acc.account_id,
                steps=tuple(acc_steps),
                gate_on_contacts=gate,
            )
        )

    # --- one application-level case for application-scoped steps (if any) ---
    app_ctx = graph._app_context()
    app_steps = tuple(
        _mk_step(s, participant_subject_id=None)
        for s in by_scope["application"]
        if _visible(s, app_ctx)
    )
    if app_steps:
        cases.append(
            PlannedCase(kind="application", subject_type="group", subject_id=None, steps=app_steps)
        )

    return cases


# ---------------------------------------------------------------------------------------------
# Graph runtime — the pure decisions a control node forces (v3)
# ---------------------------------------------------------------------------------------------


def reachable(
    graph: graph_mod.Graph, start: str | None, *, blocked: frozenset[str] = frozenset()
) -> frozenset[str]:
    """Every node id reachable from ``start`` by following edges, ``start`` included.

    ``blocked`` names nodes control may not pass THROUGH — they are excluded from the result and
    their successors are not walked. That is what lets a caller ask "what would still be reachable
    if this branch did not exist?", which is the question that stops a re-convergent arm being
    skipped just because one of the ways into it was not taken.

    Iterative and visited-guarded: a v3 graph is acyclic by publish rule, but this runs on stored
    JSON from a version that may predate that rule.
    """
    if start is None or start in blocked:
        return frozenset()
    seen: set[str] = set()
    queue = [start]
    while queue:
        current = queue.pop()
        if current in seen or current in blocked or current not in graph.nodes:
            continue
        seen.add(current)
        queue.extend(graph_mod.successors(graph, current))
    return frozenset(seen)


def control_gated_steps(graph: graph_mod.Graph) -> dict[str, str]:
    """Step ids whose release the ENGINE must hand out, mapped to the control node that gates them.

    A ``dict`` rather than a set because the gating node's id is what the held step records as its
    reason (``case_steps.data.gated_on``), which is what lets a surface say "waiting for the
    seven-day pause" instead of showing a step with no action and no explanation. Membership tests
    read the same either way.

    A step sitting directly after a control node cannot be released by ``depends_on`` alone, and
    this is the subtle failure the whole gate exists to prevent: ``graph.effective_depends_on``
    compiles "wait for the nearest step ancestor", so the step after a seven-day ``wait`` depends
    on the step BEFORE the wait — and the moment that settles, the ordinary release pass would
    instantiate it and the wait would never have happened. Same for both arms of a branch, which
    share one dependency and must not both run.

    So: a step with a control-node predecessor is held until ``orchestrator._enter_node`` walks the
    graph to it and marks it released. Empty for a legacy graph — its edges are presentation order,
    not execution order (see ``graph``'s module docstring), so nothing there is control-gated and
    every existing journey keeps releasing exactly as it did.

    **A ``condition`` holds its successors like any other control node.** It briefly did not, while
    it was a plan-time gate compiled into the step's ``visibility``; it is a two-armed branch now,
    so the step behind it is exactly the case in the paragraph above — one dependency, two arms, and
    only the arm the walk takes may run.
    """
    if graph.is_legacy_linear:
        return {}
    gated: dict[str, str] = {}
    for node in graph.nodes.values():
        if node.is_step:
            continue
        for target in graph_mod.successors(graph, node.id):
            successor = graph.nodes.get(target)
            # First gate wins: two control nodes converging on one step both hold it, and naming
            # either of them answers "why is this waiting?" — naming both would be a list nobody
            # renders.
            if successor is not None and successor.is_step and target not in gated:
                gated[target] = node.id
    return gated


def elided_by_branch(graph: graph_mod.Graph, branch_id: str, chosen: str | None) -> tuple[str, ...]:
    """The step ids an untaken branch arm has just made unreachable, in document order.

    The engine marks these ``skipped``, which is the same status plan-time visibility gives a step
    that never applied — and ``dependencies.SETTLED`` and ``derivation._relevant`` both already
    treat ``skipped`` as settled-and-irrelevant, so an untaken arm can neither block a dependent
    step nor stop the case completing. Reusing that concept rather than inventing a fourth kind of
    "not happening" is the point.

    Two subtractions make this safe, and both matter:

    * anything reachable from the CHOSEN arm is kept — two arms may re-converge on a shared
      "notify the adviser" step, and skipping it because the other arm also led there would delete
      work the case is definitely going to do;
    * anything still reachable from the entry WITHOUT passing through this branch is kept — a node
      that another live path leads to is not this branch's to elide.
    """
    node = graph.nodes.get(branch_id)
    if node is None or node.kind not in graph_mod.BRANCHING_KINDS:
        return ()
    kept = reachable(graph, chosen) | reachable(graph, graph.entry, blocked=frozenset({branch_id}))
    orphaned: set[str] = set()
    for arm in node.branches:
        if arm.next is None or arm.next == chosen:
            continue
        orphaned |= reachable(graph, arm.next)
    # Document order, not set order: the ids end up in a log line and a case's data, and a stable
    # order is what makes two runs of the same journey diffable.
    return tuple(
        nid
        for nid in graph.order
        if nid in orphaned and nid not in kept and graph.nodes[nid].is_step
    )


@dataclass(frozen=True)
class GateContext:
    """Whether each account case's cross-case gate is currently open, given which contacts' profile
    cases have completed. Pure helper the orchestrator uses to (re)evaluate release."""

    completed_profile_contacts: frozenset[int] = field(default_factory=frozenset)

    def gate_open(self, case: PlannedCase) -> bool:
        return all(cid in self.completed_profile_contacts for cid in case.gate_on_contacts)
