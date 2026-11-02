"""What needs a person in the AML console, and why.

The unit of work is the client, keyed as ``CLIENT_KEY`` says. A client needs attention exactly when
one of its ``REASONS`` holds, and each reason opens one of the client's ``STEPS``. ``client_rows``
works out, per reason, how many there are, how long the oldest has waited and the check it is on;
``reasons_of``, ``steps_of`` and ``age_of`` turn that row into what the console draws, so the client
list, the client page and the work queue all give the same answer.

The rule over one check is ``NEEDS_HUMAN``: the OR of its named branches. A settled AML refer (every
match decided, the journey allowed) and an identity refer an operator asked new photos for stay
``refer`` but need nobody.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.complycube import tasks

# A client's name over `c` (crm.contacts) and `cl` (compliance.clients): the firm's own record
# as the client record prints it (`tasks.CONTACT_NAME`), then what ComplyCube holds. What the
# client list shows and what `q` searches.
CLIENT_NAME = (
    f"COALESCE({tasks.CONTACT_NAME}, NULLIF(cl.entity_name, ''), "
    "NULLIF(trim(concat_ws(' ', cl.first_name, cl.last_name)), ''), "
    "NULLIF(cl.company_name, ''), '')"
)

#: A client's key (``key`` on a list row, ``GET /aml/clients/{key}``) is the uid of its
#: ``crm.contacts`` record, the id the rest of admin uses for a client, whether or not it has a
#: ``compliance.clients`` row yet. A partner-API client with no contact is keyed by its
#: ``compliance.clients`` uid. The SQL form, over ``c`` (crm.contacts) and ``cl``
#: (compliance.clients).
CLIENT_KEY = "COALESCE(c.uid, cl.uid)"

#: A client's type, ``individual`` or ``organisation``: the firm's record (``crm.contacts.type``),
#: else ComplyCube's (``person`` / ``company``). Over ``c`` and ``cl``, as ``CLIENT_NAME``.
CLIENT_TYPE = (
    "COALESCE(c.type::text, CASE cl.client_type WHEN 'company' THEN 'organisation' "
    "WHEN 'person' THEN 'individual' END)"
)

# ComplyCube's overall level as stored, or the profile's `overallRisk` where the stored level is
# `not_set`: ComplyCube's reference names the field `overallRisk` and its example `overall`.
RISK_LEVEL = (
    "CASE WHEN r.client_id IS NULL THEN NULL "
    "WHEN r.overall = 'not_set' AND COALESCE(r.profile ->> 'overallRisk', '') <> '' "
    "THEN r.profile ->> 'overallRisk' ELSE r.overall END"
)

# The branches of the rule over one check, over the alias `h`. `:give_up` is
# `poller.GIVE_UP` in seconds: the poller stops asking about a check after it.
#: An identity refer nobody has decided.
IDENTITY_REFER = "(h.kind = 'identity' AND h.status = 'refer' AND h.decided_at IS NULL)"
_UNDECIDED_MATCH = (
    "EXISTS (SELECT 1 FROM compliance.matches m WHERE m.check_id = h.id AND m.decision IS NULL)"
)
#: An AML refer with a match nobody has decided.
OPEN_HITS = f"(h.kind = 'aml' AND h.status = 'refer' AND {_UNDECIDED_MATCH})"
#: A portal AML refer with every match decided whose journey nobody has allowed or stopped.
HELD = (
    "(h.kind = 'aml' AND h.status = 'refer' AND h.origin = 'portal' AND h.decided_at IS NULL "
    f"AND NOT {_UNDECIDED_MATCH})"
)
#: A check ComplyCube could not run that nobody has acted on.
FAILED = "(h.status = 'failed' AND h.decided_at IS NULL)"
#: A check still pending after the poller stopped asking about it.
NO_ANSWER = "(h.status = 'pending' AND h.created_at <= now() - make_interval(secs => :give_up))"
#: A check that needs a human.
NEEDS_HUMAN = f"({IDENTITY_REFER} OR {OPEN_HITS} OR {HELD} OR {FAILED} OR {NO_ANSWER})"

StepKey = Literal["identity", "screened", "monitoring", "hits", "risk"]
StepState = Literal["done", "open", "waiting", "advisory", "none"]

#: A client's steps, in the order the console draws them.
STEPS: dict[StepKey, str] = {
    "identity": "Identity",
    "screened": "Screened",
    "monitoring": "Monitoring",
    "hits": "Hits reviewed",
    "risk": "Risk rating",
}


@dataclass(frozen=True)
class Reason:
    """Why a client needs a person: the step it opens and the SQL that finds it. ``where`` is over
    one check ``h`` (and ``hm``, its matches); ``count`` and ``since`` are the aggregates over the
    checks it holds for. A reason with no ``where`` is the client's own (``_CLIENT_REASONS``)."""

    label: str
    step: StepKey
    where: str | None = None
    count: str = "count(*)"
    since: str = "h.created_at"


#: Every reason, in the order the console lists them. ``since`` is when the wait started: the
#: check's creation, the monitoring alert (``compliance.checks.alerted_at``), the oldest undecided
#: match, the last match decision on a held check.
REASONS: dict[str, Reason] = {
    "identity_refer": Reason("Identity to decide", "identity", IDENTITY_REFER),
    "identity_failed": Reason(
        "Identity check failed", "identity", f"(h.kind = 'identity' AND {FAILED})"
    ),
    "identity_no_answer": Reason(
        "No answer on an identity check", "identity", f"(h.kind = 'identity' AND {NO_ANSWER})"
    ),
    "screen_due": Reason("Screen to run", "screened"),
    "screen_stale": Reason("Screened details changed", "screened"),
    "screen_failed": Reason("Screen failed", "screened", f"(h.kind = 'aml' AND {FAILED})"),
    "screen_no_answer": Reason(
        "No answer on a screen", "screened", f"(h.kind = 'aml' AND {NO_ANSWER})"
    ),
    # A label on an open AML refer, never a reason of its own: it holds only with `open_hits` or
    # `held` on the same check.
    "monitoring_alert": Reason(
        "Monitoring alert",
        "monitoring",
        f"(h.kind = 'aml' AND h.alerted_at IS NOT NULL AND ({OPEN_HITS} OR {HELD}))",
        since="h.alerted_at",
    ),
    "open_hits": Reason(
        "Hits to review", "hits", OPEN_HITS, count="sum(hm.open)", since="hm.open_since"
    ),
    "held": Reason(
        "Held on a confirmed match", "hits", HELD, since="COALESCE(hm.decided_last, h.updated_at)"
    ),
}

# The client's own reasons, over the row's `aw` (awaiting) and `amc` (latest AML check): a portal
# client due a screen the worker has not queued, and a latest screen whose details changed since.
_DUE = "(aw.contact_id IS NOT NULL AND NOT aw.queued)"
_CLIENT_REASONS = {
    "screen_due": (_DUE, "aw.updated_at", "NULL::text"),
    "screen_stale": ("COALESCE(amc.stale, false)", "amc.stale_at", "amc.uid"),
}


def _reason_columns() -> str:
    """Three columns per reason: ``<code>_count``, ``<code>_since`` (the oldest wait) and
    ``<code>_check`` (the check that has waited longest; null for a screen due)."""
    columns: list[str] = []
    for code, reason in REASONS.items():
        if reason.where is None:
            found, since, check = _CLIENT_REASONS[code]
            columns.append(f"CASE WHEN {found} THEN 1 ELSE 0 END AS {code}_count")
            columns.append(f"CASE WHEN {found} THEN {since} END AS {code}_since")
            columns.append(f"CASE WHEN {found} THEN {check} END AS {code}_check")
        else:
            columns.append(f"COALESCE(w.{code}_count, 0) AS {code}_count")
            columns.append(f"w.{code}_since AS {code}_since")
            columns.append(f"w.{code}_check AS {code}_check")
    return ",\n       ".join(columns)


def _check_aggregates() -> str:
    """Per check-borne reason, its count, oldest wait and longest-waiting check over the client's
    checks ``h``; plus ``human``, whether any check needs one (``NEEDS_HUMAN``)."""
    columns = [f"bool_or({NEEDS_HUMAN}) AS human"]
    for code, reason in REASONS.items():
        if reason.where is None:
            continue
        columns.append(f"{reason.count} FILTER (WHERE {reason.where}) AS {code}_count")
        columns.append(f"min({reason.since}) FILTER (WHERE {reason.where}) AS {code}_since")
        columns.append(
            f"(array_agg(h.uid ORDER BY {reason.since}, h.id) FILTER (WHERE {reason.where}))[1] "
            f"AS {code}_check"
        )
    return ",\n           ".join(columns)


def _awaiting(narrow: bool) -> str:
    """A portal client is due a screen while an AML kyc row of theirs is pending and no check holds
    it. ``queued``: the worker already has the screen queued."""
    one = "AND k.tenant_id = :tenant AND k.contact_id = CAST(:contact AS int)" if narrow else ""
    return f"""
SELECT a.*, EXISTS (
         SELECT 1 FROM flows.outbox o
          WHERE o.tenant_id = a.tenant_id AND o.event_type = 'aml.screen.run'
            AND o.status = 'queued'
            AND o.payload @> jsonb_build_object('contact_id', a.contact_id)
       ) AS queued
  FROM (
    SELECT k.tenant_id, k.contact_id, max(k.updated_at) AS updated_at
      FROM crm.kyc_checks k
      JOIN crm.contacts c ON c.id = k.contact_id AND c.deleted_at IS NULL
     WHERE k.kind = 'aml' AND k.status = 'pending'
       AND NOT EXISTS (SELECT 1 FROM compliance.checks x WHERE x.kyc_check_id = k.id)
       {one}
     GROUP BY k.tenant_id, k.contact_id
  ) a
"""  # noqa: S608 - the only interpolation is the fixed narrowing above


def _waiting_since() -> str:
    """The oldest wait over every reason. ``LEAST`` skips nulls, so a reason not held counts for
    nothing; null when none holds."""
    return "LEAST(" + ", ".join(f"{code}_since" for code in REASONS) + ")"


def client_rows(*, narrow: bool = False) -> str:
    """One row per client: every compliance client with a check or due a screen, then every portal
    client due a first screen who has no compliance client yet. A compliance client with no firm
    (an unmatched provider object) is not work for anyone here and is left out.

    Binds ``:give_up`` (``poller.GIVE_UP`` in seconds). ``narrow`` reads one client: it binds
    ``:tenant``, ``:client`` (the compliance client id, or null) and ``:contact`` (the contact id,
    or null), and pushes them into the scan so one read stays cheap.
    """
    one_client = "AND cl.id = CAST(:client AS int)" if narrow else ""
    one_contact = (
        "AND a.tenant_id = :tenant AND a.contact_id = CAST(:contact AS int)" if narrow else ""
    )
    return f"""
WITH awaiting AS ({_awaiting(narrow)}),
people AS (
  SELECT cl.id AS client_id, cl.tenant_id, cl.contact_id
    FROM compliance.clients cl
   WHERE cl.tenant_id IS NOT NULL {one_client}
     AND (EXISTS (SELECT 1 FROM compliance.checks ch WHERE ch.client_id = cl.id)
          OR EXISTS (SELECT 1 FROM awaiting a
                      WHERE a.tenant_id = cl.tenant_id AND a.contact_id = cl.contact_id))
  UNION ALL
  SELECT NULL::int, a.tenant_id, a.contact_id
    FROM awaiting a
   WHERE NOT EXISTS (SELECT 1 FROM compliance.clients cl
                      WHERE cl.tenant_id = a.tenant_id AND cl.contact_id = a.contact_id)
     {one_contact}
),
listed AS (
SELECT {CLIENT_KEY} AS key, p.tenant_id, p.client_id, c.uid AS contact_uid,
       {CLIENT_NAME} AS client_name, {CLIENT_TYPE} AS client_type,
       t.uid AS tenant_uid, t.name AS tenant_name,
       (aw.contact_id IS NOT NULL OR EXISTS (
          SELECT 1 FROM compliance.checks o WHERE o.client_id = p.client_id AND o.origin = 'portal'
       )) AS portal,
       idc.uid AS identity_uid, idc.status AS identity_status, idc.attempt AS identity_attempt,
       idc.decided AS identity_decided, idc.updated_at AS identity_updated_at,
       amc.uid AS aml_uid, amc.status AS aml_status, amc.decided AS aml_decided,
       amc.monitoring AS aml_monitoring, amc.updated_at AS aml_updated_at,
       (aw.contact_id IS NOT NULL) AS awaiting, aw.updated_at AS awaiting_since,
       COALESCE(aw.queued, false) AS screen_queued,
       COALESCE(amc.stale, false) AS stale,
       {RISK_LEVEL} AS risk,
       ({_DUE} OR COALESCE(amc.stale, false) OR COALESCE(w.human, false)) AS needs_attention,
       {_reason_columns()},
       GREATEST(idc.updated_at, amc.updated_at, aw.updated_at) AS updated_at
  FROM people p
  JOIN auth.tenants t ON t.id = p.tenant_id
  LEFT JOIN compliance.clients cl ON cl.id = p.client_id
  LEFT JOIN crm.contacts c ON c.id = p.contact_id
  LEFT JOIN awaiting aw ON aw.tenant_id = p.tenant_id AND aw.contact_id = p.contact_id
  LEFT JOIN LATERAL (
    SELECT ch.uid, ch.status, ch.attempt, (ch.decided_at IS NOT NULL) AS decided, ch.updated_at
      FROM compliance.checks ch
     WHERE ch.client_id = p.client_id AND ch.kind = 'identity'
     ORDER BY ch.id DESC LIMIT 1
  ) idc ON true
  LEFT JOIN LATERAL (
    SELECT ch.uid, ch.status, (ch.decided_at IS NOT NULL) AS decided,
           (ch.stale_at IS NOT NULL) AS stale, ch.stale_at, ch.monitoring, ch.updated_at
      FROM compliance.checks ch
     WHERE ch.client_id = p.client_id AND ch.kind = 'aml'
     ORDER BY ch.id DESC LIMIT 1
  ) amc ON true
  LEFT JOIN LATERAL (
    SELECT {_check_aggregates()}
      FROM compliance.checks h
      LEFT JOIN LATERAL (
        SELECT count(*) FILTER (WHERE m.decision IS NULL) AS open,
               min(m.created_at) FILTER (WHERE m.decision IS NULL) AS open_since,
               max(m.decided_at) AS decided_last
          FROM compliance.matches m
         WHERE m.check_id = h.id
      ) hm ON true
     WHERE h.client_id = p.client_id
  ) w ON true
  LEFT JOIN compliance.client_risk r ON r.client_id = p.client_id
)
SELECT listed.*, {_waiting_since()} AS waiting_since FROM listed
"""  # noqa: S608 - the only interpolations are the fixed SQL fragments above


#: Every client, one row each (``client_rows``).
CLIENT_ROWS = client_rows()
#: One client's row (``client_rows(narrow=True)``).
ONE_CLIENT_ROW = client_rows(narrow=True)


#: The work queue's reason filter, keyed as the console's "Show" list keys it, and the reasons
#: each one takes in. A CLOSED SET: the key arrives from a query string.
WORK_FILTERS: dict[str, tuple[str, ...]] = {
    "hits": ("open_hits",),
    "identity": ("identity_refer", "identity_failed", "identity_no_answer"),
    "screens": ("screen_due", "screen_stale", "screen_failed", "screen_no_answer"),
    "monitoring": ("monitoring_alert",),
    "held": ("held",),
}


def work_filter(key: str, alias: str = "q") -> str:
    """The SQL that keeps a row holding one of a ``WORK_FILTERS`` key's reasons, over ``alias``."""
    return "(" + " OR ".join(f"{alias}.{code}_count > 0" for code in WORK_FILTERS[key]) + ")"


def filters_of(row: Mapping[Any, Any]) -> list[str]:
    """The ``WORK_FILTERS`` keys a client row falls under."""
    return [
        key
        for key, codes in WORK_FILTERS.items()
        if any(int(row.get(f"{code}_count") or 0) > 0 for code in codes)
    ]


def reasons_of(row: Mapping[Any, Any]) -> list[dict[str, object]]:
    """The reasons a client row holds, in ``REASONS`` order: each one's code, label, step, count,
    the oldest wait (``since``) and the check that has waited longest (``check_uid``)."""
    found: list[dict[str, object]] = []
    for code, reason in REASONS.items():
        count = int(row.get(f"{code}_count") or 0)
        if count <= 0:
            continue
        found.append(
            {
                "code": code,
                "label": reason.label,
                "step": reason.step,
                "count": count,
                "since": row.get(f"{code}_since"),
                "check_uid": row.get(f"{code}_check"),
            }
        )
    return found


def _step(
    key: StepKey, state: StepState, *, count: int | None = None, hint: str | None = None
) -> dict[str, object]:
    return {"key": key, "label": STEPS[key], "state": state, "count": count, "hint": hint}


#: The AML statuses of a screen that ran: its hits can be reviewed and it can be monitored.
_SCREENED = frozenset({"clear", "refer", "rejected"})


def steps_of(
    row: Mapping[Any, Any], reasons: list[dict[str, object]] | None = None
) -> list[dict[str, object]]:
    """The client's steps, in ``STEPS`` order. A step is ``open`` exactly when a reason on it holds,
    so a client needs attention exactly when one of its steps is open. Otherwise its state comes
    from the latest check of its kind: ``waiting`` while something is in flight, ``advisory`` for
    what does not block (monitoring off), ``done``, or ``none`` when there is nothing yet."""
    held = reasons if reasons is not None else reasons_of(row)
    on_step: dict[str, list[dict[str, object]]] = {}
    for reason in held:
        on_step.setdefault(str(reason["step"]), []).append(reason)

    def opened(key: StepKey) -> dict[str, object] | None:
        found = on_step.get(key)
        if not found:
            return None
        count = sum(int(str(reason["count"])) for reason in found)
        return _step(key, "open", count=count, hint=", ".join(str(r["label"]) for r in found))

    identity_status = row.get("identity_status")
    identity_decided = bool(row.get("identity_decided"))
    aml_status = None if row.get("awaiting") else row.get("aml_status")
    screened = aml_status in _SCREENED

    identity = opened("identity")
    if identity is None:
        if identity_status is None:
            identity = _step("identity", "none")
        elif identity_status == "pending":
            identity = _step("identity", "waiting", hint="Waiting for ComplyCube")
        elif identity_status == "refer" and identity_decided:
            identity = _step("identity", "waiting", hint="New photos asked for")
        else:
            identity = _step("identity", "done")

    screen = opened("screened")
    if screen is None:
        if row.get("awaiting"):
            screen = _step("screened", "waiting", hint="Screen queued")
        elif aml_status is None:
            screen = _step("screened", "none")
        elif aml_status == "pending":
            screen = _step("screened", "waiting", hint="Waiting for ComplyCube")
        else:
            screen = _step("screened", "done")

    monitoring = opened("monitoring")
    if monitoring is None:
        if not screened:
            monitoring = _step("monitoring", "none")
        elif row.get("aml_monitoring"):
            monitoring = _step("monitoring", "done")
        else:
            monitoring = _step("monitoring", "advisory", hint="Monitoring is off")

    hits = opened("hits")
    if hits is None:
        hits = _step("hits", "done" if screened else "none")

    risk_level = row.get("risk")
    if risk_level and risk_level != "not_set":
        risk = _step("risk", "done", hint=str(risk_level))
    elif row.get("client_id") is None:
        risk = _step("risk", "none")
    else:
        risk = _step("risk", "waiting", hint="ComplyCube has not rated this client")

    return [identity, screen, monitoring, hits, risk]


AgeBand = Literal["fresh", "ageing", "overdue"]
#: Working days a wait may run before it is ``ageing`` (amber), and before it is ``overdue`` (red).
AGEING_AFTER = 2
OVERDUE_AFTER = 5
_LONDON = ZoneInfo("Europe/London")


def working_days_between(start: dt.date, end: dt.date) -> int:
    """The Monday–Friday days after ``start`` up to and including ``end``; 0 when ``end`` is not
    after ``start``. Bank holidays are not known here."""
    if end <= start:
        return 0
    weeks, extra = divmod((end - start).days, 7)
    days = weeks * 5
    for offset in range(1, extra + 1):
        if (start + dt.timedelta(days=offset)).weekday() < 5:
            days += 1
    return days


def age_of(since: dt.datetime | None, now: dt.datetime | None = None) -> dict[str, object] | None:
    """How long a wait has run until ``now`` (default: this moment), in working days counted on
    London dates, and its band: ``fresh``, ``ageing`` after ``AGEING_AFTER`` days, ``overdue``
    after ``OVERDUE_AFTER``. None when nothing is waiting."""
    if since is None:
        return None
    end = now if now is not None else dt.datetime.now(dt.UTC)
    days = working_days_between(since.astimezone(_LONDON).date(), end.astimezone(_LONDON).date())
    band: AgeBand = (
        "overdue" if days > OVERDUE_AFTER else "ageing" if days > AGEING_AFTER else "fresh"
    )
    return {"working_days": days, "band": band}
