"""``ref.employee_users`` — the EMAIL for a SEI employee, which the nightly drop has not got.

**Why this model exists, stated precisely, because 46 of the 47 reports need no API help.**
The drop's ``UserDetail`` report carries the complete employee population and almost every
field a picker or a profile wants:

===============================  ==================================================
``UserDetail`` column            what it gives
===============================  ==================================================
``ENTITY_ID``                    the id enrolment sends as ``employeeEntityId``
``USER_DISPLAY_NAME``            one field holding ``"Armasoiu, Maria"``
``LOGIN_USER_ID``                the SWP login name
``FIRM_ID``                      which firm
``USER_TYPE``                    1 Employee / 2 End Client / 3 Data Integration
``USER_STATUS``                  Y/N — active at SEI
``LOGIN_PHONE``                  a mobile number
``MONEY_MOVEMENT_LIMIT``         their payment authority
===============================  ==================================================

**What it does not carry is an address of any kind** — twelve columns, no email. And the email
is the whole hinge of the sync: ``auth.merge_provider_user`` (0123) ATTACHES a SEI user to the
Graphene login the same person already holds by matching on it, and can only create a
disconnected placeholder without one. So this is the narrowest possible reach for the API — one
field, for one purpose — and it is exactly the shape ``ref.enrollment_templates`` already has.

``GET /employeeservice/firmemployeeusers`` also splits the name into ``firstName``/
``middleName``/``lastName``, where the report has ``"Last, First"`` in a single field. Both are
published: the split is better and the report is the fallback when the API is off.

Disabled by default: a blank ``SEI_API_BASE_URL`` returns an empty typed table, so a deployment
with SFTP credentials and no API credentials runs unchanged rather than failing every night —
and ``platform.users`` LEFT JOINs this, so the population still lands, just without emails.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

import pyarrow as pa
from interlace import model

from sei_sync.firms import resolve_firm_ids
from sei_sync.previous import previous_table
from sei_sync.pull import fetch_employee_users
from sei_sync.settings import SeiSettings

logger = logging.getLogger("sei_sync.models")

#: The conformed shape. Ids stay VARCHAR for the reason the whole raw layer does: they are join
#: keys, never arithmetic. `unique_user_id` is an INTEGER in SEI's own spec while the drop's
#: `ENTITY_ID` is text, and this is the column the two are joined on — so it is stringified here
#: rather than at the join, where a type that depends on which source answered is unjoinable.
SCHEMA: dict[str, pa.DataType] = {
    "entity_id": pa.string(),
    "firm_id": pa.string(),
    "email": pa.string(),
    "first_name": pa.string(),
    "middle_name": pa.string(),
    "last_name": pa.string(),
    "login_name": pa.string(),
    "phone": pa.string(),
    "crd_number": pa.string(),
    "state": pa.string(),
    "as_of_date": pa.date32(),
    "fetched_at": pa.timestamp("us", tz="UTC"),
}

#: SEI's key for each conformed column. A single mapping rather than the alias LIST
#: `api_templates` needs, because one service answers this and its spec names every field.
_KEYS: dict[str, str] = {
    "entity_id": "uniqueUserId",
    "firm_id": "firmId",
    "email": "emailId",
    "first_name": "firstName",
    "middle_name": "middleName",
    "last_name": "lastName",
    "login_name": "loginUserName",
    "phone": "phone",
    "crd_number": "crdNumber",
    "state": "state",
}


def _text(row: dict[str, Any], key: str) -> str | None:
    """One field, stringified and blank-normalised. SEI sends an integer id and a numeric CRD."""
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def conform(rows: list[dict[str, Any]], *, as_of: date, fetched_at: datetime) -> pa.Table:
    """SEI's employee-user rows -> the conformed table.

    A row with no ``uniqueUserId`` is DROPPED and counted: that id is the only thing joining
    this to the drop's population, so a row without one cannot contribute the email it exists
    to contribute — and keeping it would put a NULL in the join key.
    """
    columns: dict[str, list[Any]] = {name: [] for name in SCHEMA}
    dropped = 0
    for row in rows:
        entity_id = _text(row, _KEYS["entity_id"])
        if entity_id is None:
            dropped += 1
            continue
        columns["entity_id"].append(entity_id)
        for name, key in _KEYS.items():
            if name == "entity_id":
                continue
            columns[name].append(_text(row, key))
        columns["as_of_date"].append(as_of)
        columns["fetched_at"].append(fetched_at)
    if dropped:
        logger.warning("sei ref.employee_users: dropped %d row(s) with no uniqueUserId", dropped)
    return pa.table({name: pa.array(values, type=SCHEMA[name]) for name, values in columns.items()})


@model(
    name="ref.employee_users",
    # `full_merge`, not `merge`, and for `ref.enrollment_templates`' reason: this is a full
    # statement of who the credential can see TODAY. A user SEI has removed must stop appearing,
    # where merging would leave a departed person's email in the attach rule for ever — and an
    # attach is a mapping onto a real login, so a stale one is worse than a missing one.
    # `replace` would drop the table mid-apply.
    strategy="full_merge",
    key=("entity_id",),
    # The firm list is the Reference report. The processing org is still an API read
    # per firm. The last built `ref.firms` is what a run sees when today's drop failed.
    depends_on=("ref.firms",),
    schedule={"cron": "40 7 * * *"},
    tags=("sei", "ref", "api"),
    description="SEI employee users per firm — the email the UserDetail report has not got",
)
async def ref_employee_users(this: Any = None, ref_firms: Any = None) -> pa.Table:
    settings = SeiSettings()
    now = datetime.now(tz=UTC)
    if not settings.api_base_url:
        logger.info("sei ref.employee_users: SEI_API_BASE_URL unset — API sync disabled")
        return conform([], as_of=now.date(), fetched_at=now)

    # `processingOrgId` + `serviceId` are required on i31. The org is read from
    # `firm-default-attr-cache` for each firm in the Reference report (this delivery
    # group shares 3100). Service 1 is the widest filter; a DSR tag 400s.
    rows, stats = await fetch_employee_users(settings, firm_ids=resolve_firm_ids(ref_firms))
    if stats.failures:
        # Not raised: partial reference data is worth having. A page that failed means those
        # users have no email THIS run and land as placeholders, which the next run corrects.
        logger.warning(
            "sei ref.employee_users: %d page read(s) failed, e.g. %s",
            len(stats.failures),
            next(iter(stats.failures.items())),
        )
    # REFUSING TO DELETE ALL ROWS — same guard as the listing extractors. An empty
    # answer while every page failed is a read that did not happen (SEI allowlists by
    # source IP), not "this credential has no employees". After a rebuild GC the previous
    # snapshot may be gone; that is not existing rows to protect, so an empty first write
    # is allowed and the next successful run fills it.
    if not rows and stats.failures:
        existing = previous_table(this)
        if existing is not None and existing.num_rows > 0:
            raise RuntimeError(
                f"sei ref.employee_users: every page read failed ({len(stats.failures)}); "
                "refusing to delete all rows"
            )
        logger.warning(
            "sei ref.employee_users: every page read failed (%d) and there is no existing "
            "table to protect — continuing empty",
            len(stats.failures),
        )
    return conform(rows, as_of=now.date(), fetched_at=now)


__all__ = ["SCHEMA", "conform", "ref_employee_users"]
