"""Push a Graphene client — and their pension accounts — into Morgan Lloyd.

One idempotent action behind `POST /crm/contacts/{id}/sync-to-morgan-lloyd` and the SIPP account
record's "Sync to Morgan Lloyd". It exists because the things a drawdown and a valuation need
are created by separate Dynamics calls, in order, and each has to be recorded locally so the
next run knows it is done:

    1. the ADVISER     `cam_CreateFinancialAdviserContact` -> a Dynamics GUID, kept on
       (the client's)                             `auth.users.provider_refs['morgan-lloyd']`.
                                                  `platform.links.entity_id` is bigint and a
                                                  login is a uuid, so the staff-profile column
                                                  is the mapping (0131).
    2. the CONTACT     `cam_createcontact`  -> a Dynamics GUID, kept on `platform.links`
    3. a SCHEME per    `cam_createscheme`   -> a scheme reference (`MLSIPP/00001`), kept the
       pension account                         same way against the account (`primary`)
    4. a POLICY per    `cam_createwrapper`  -> a policy number, WHICH WE MINT (the account's
       pension account                         uid), kept against the account as `policy`
    5. BENEFICIARIES   `cam_createbeneficiaries` -> a nomination GUID, kept on
       on that scheme                         `platform.links` (`entity_type='beneficiary'`)

The adviser firm (`settings.adviser_firm_id`) is still a prerequisite: without it the contact is
created unattached and every later `cam_createscheme` 403s. The adviser CONTACT used to be
omitted because `cam_CreateFinancialAdviserContact` answered `does not have CreateAccess
right(s)` for our service principal. That permission was granted in September 2026, and the
client's adviser is now a step — created once per person, reused via `provider_refs`, and
attached to the contact. The deployment's `adviser_contact_id` remains the fallback when the
client has nobody on the book.

**THE SCHEME AND THE POLICY ARE DIFFERENT IDENTIFIERS AND THE DIFFERENCE IS LOAD-BEARING.** Every
scoped action — contributions, transfers, benefits — is addressed by the scheme reference; a
VALUATION is filed against the policy, and `cam_createpolicyvaluation` accepts nothing else. Step
3 did not exist, so nothing in the estate had ever recorded a policy number — which is why
`services/morgan-lloyd/models/sink_valuations.py` returns `skipped` for every scheme on every
run, and says so in its own docstring. Sending the scheme reference in its place is the
`cam_schemerefs` mistake again: a plausible identifier of the wrong kind, refused with a message
that reads as "not found" — for a valuation, the vendor's exact words are
`Failed Policies: <ref> (missing policy)`.

**THE POLICY DOES NOT EXIST THE MOMENT `cam_createwrapper` ANSWERS.** Dynamics creates it
asynchronously — measured at roughly two minutes on the dev org — and until then the number
resolves to nothing: `cam_policies` returns no row for it and a valuation filed against it is
refused as `(missing policy)`. The action's own answer is no help, because it is
`{"wrapper": "<number>", "response": {"responseKey": 0, "message": "<number> Policy Exists"}}`
whether the policy has materialised or not. This cost a rewrite: the immediate read came back
empty, which reads as "the create did nothing", and the design was reworked around a race before a
second look found the policy sitting there two minutes later. Nothing in this module waits for it
— the link is the number we SENT, so the sync is unaffected — but anything valuing an account it
has just created has to allow for the lag.

IDEMPOTENCE IS THE POINT. Before this, `POST /crm/contacts/{id}/morgan-lloyd` created a contact,
failed to read the id out of the action envelope, and recorded nothing — so the next attempt
created the person again. Every step here checks `platform.links` first and reports `skipped`,
so running it twice is safe and running it after a partial failure resumes rather than
duplicates.

WHAT IT DOES NOT DO. It does not create the adviser as a Dynamics contact, and it does not push
non-pension accounts — a GIA or an ISA has no scheme in Morgan Lloyd's model, so there is nothing
to create. A Seccl SIPP is administered by Seccl, so it is not pushed either: Morgan Lloyd
administers SEI SIPPs. The adviser omission was once forced
(`cam_CreateFinancialAdviserContact` answered `does not have CreateAccess right(s)` for our
service principal) and is now a CHOICE: the
permission was granted in September 2026, but an adviser is a firm-level record created once, not
per client, so a per-client sync is the wrong place to mint one. The live suite creates it
(`pkgs/morgan-lloyd/tests/integration/anchors.py::ensure_adviser`).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from datetime import date
from typing import Any

import httpx
from morgan_lloyd import MorganLloydError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.morgan_lloyd.benefits import (
    UNAVAILABLE,
    MorganLloydUnavailable,
    _explain,
    _settings,
    configured,
    unconfigured,
)
from app.integrations.morgan_lloyd.contacts import (
    _title_code,
    client_columns,
    create_contact,
    missing_fields,
)
from app.providers import custodians

logger = logging.getLogger(__name__)

# Only a pension has a scheme in Morgan Lloyd's model. Mirrors `routers/crm._PENSION_PRODUCTS`.
PENSION_PRODUCTS = frozenset({"SIPP", "PENSION", "SSAS"})


def _morgan_lloyd_account(account: Mapping[Any, Any]) -> bool:
    """An SEI SIPP. A Seccl SIPP is administered by Seccl, so it has no scheme to create."""
    product = str(account.get("product_type") or "").upper()
    return product in PENSION_PRODUCTS and (account.get("custodian") or "") == "sei"


# `cam_createscheme` requires these two alongside contactId. ML's SIPP covers scheme types 16 and
# 17; the sipp_type values are documented without labels and are firm-specific, so they are
# settings rather than constants guessed here.
# noqa below: `client_columns` returns a literal built from column names of ours -- no request
# value reaches it. Same posture as `app.contacts.phone_expr`'s other callers.
_CLIENT_SQL = (
    "SELECT " + client_columns("c") + ", "  # noqa: S608
    "       c.tenant_id, "
    "       pl.external_id AS ml_contact_id "
    "FROM crm.contacts c "
    "LEFT JOIN platform.links pl "
    "  ON pl.entity_type = 'contact' AND pl.entity_id = c.id "
    " AND pl.provider = 'morgan-lloyd' AND pl.link_type = 'primary' "
    "WHERE c.id = :cid AND c.deleted_at IS NULL"
)

# A pension account's two Morgan Lloyd link types, and they are NOT interchangeable:
#
#   primary -> the SCHEME reference (`MLSIPP/01417`), which the vendor mints and which every
#              scoped action (contributions, transfers, benefits) is addressed by;
#   policy  -> the POLICY number, which `cam_createwrapper` takes and WE mint, and which is the
#              only identifier `cam_createpolicyvaluation` accepts.
#
# Sending one where the other belongs is the `cam_schemerefs` mistake in another costume: a
# plausible identifier of the wrong kind, refused with a message that reads as "not found". Two
# link types rather than two columns because `platform.links`' key already carries `link_type`
# (0003, widened by 0066) -- so this needs no migration.
LINK_SCHEME = "primary"
LINK_POLICY = "policy"
LINK_BENEFICIARY = "primary"

_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

# `p.data` is the product's own config; the ML scheme/SIPP type live on the PRODUCT rather than on
# the deployment because ML's SIPP covers scheme types 16 AND 17, and the values are firm-specific
# option-set numbers the vendor documents without labels. A firm offering two SIPP products must
# be able to say which is which; `MORGAN_LLOYD_SCHEME_TYPE`/`_SIPP_TYPE` remain the fallback for
# the firm that offers one.
_ACCOUNTS_SQL = """
SELECT a.id, a.uid, a.name,
       COALESCE(a.custodian::text, (
         SELECT l.provider::text FROM platform.links l
          WHERE l.tenant_id = a.tenant_id
            AND l.entity_type = 'account' AND l.entity_id = a.id
            AND l.link_type = 'primary' AND l.provider = ANY(:custodians)
          ORDER BY l.provider
          LIMIT 1)) AS custodian,
       a.value::float8 AS value,
       p.product_type,
       pp.data->>'ml_scheme_type' AS product_scheme_type,
       pp.data->>'ml_sipp_type'   AS product_sipp_type,
       spl.external_id AS ml_scheme_ref,
       ppl.external_id AS ml_policy_ref
FROM platform.accounts a
LEFT JOIN platform.products p ON p.id = a.product_id
-- 0131: the provider IS the value, so this is one predicate rather than a join through a
-- registry table that had to be seeded before it could resolve.
LEFT JOIN platform.product_platforms pp
  ON pp.product_id = p.id AND pp.provider = 'morgan-lloyd'
LEFT JOIN platform.links spl
  ON spl.entity_type = 'account' AND spl.entity_id = a.id
 AND spl.provider = 'morgan-lloyd' AND spl.link_type = 'primary'
LEFT JOIN platform.links ppl
  ON ppl.entity_type = 'account' AND ppl.entity_id = a.id
 AND ppl.provider = 'morgan-lloyd' AND ppl.link_type = 'policy'
WHERE a.contact_id = :cid
ORDER BY a.id
"""

# The client's adviser. `role = 'adviser'` is unique per contact (0245) and is the one presented
# as primary throughout the portals; a client can have several staff relationships and only one
# of those is the person Morgan Lloyd should name on the contact.
_ADVISER_SQL = """
SELECT u.id::text AS user_id, u.first_name, u.last_name, u.email,
       u.provider_refs->>'morgan-lloyd' AS ml_adviser_id
FROM crm.client_advisers ca
JOIN auth.users u ON u.id = ca.adviser_id
WHERE ca.contact_id = :cid AND u.deleted_at IS NULL AND ca.role = 'adviser'
"""

# THE MAPPING LIVES ON THE LOGIN, not on `platform.links`. `entity_id` is bigint and
# `auth.users.id` is a uuid, which is exactly why 0131 put `provider_refs` on the user.
#
# `CAST(:ext AS text)` is load-bearing. `jsonb_build_object` is VARIADIC "any"; an untyped
# bind is `could not determine data type of parameter $1`, which is a 500 AFTER
# `cam_CreateFinancialAdviserContact` has already minted a person Morgan Lloyd cannot delete.
_PROVIDER_REFS_SQL = """
UPDATE auth.users
   SET provider_refs = COALESCE(provider_refs, '{}'::jsonb)
                    || jsonb_build_object('morgan-lloyd', CAST(:ext AS text))
 WHERE id = CAST(:u AS uuid) AND tenant_id = :tenant_id
"""

# Nominations hang off the ACCOUNT (`crm.beneficiaries`, 0139) because that is the grain
# `cam_createbeneficiaries` is addressed by.
_BENEFICIARIES_SQL = """
SELECT b.id, b.uid, b.title, b.name, b.relationship, b.proportion, b.nominated_on, b.address,
       pl.external_id AS ml_beneficiary_id
FROM crm.beneficiaries b
LEFT JOIN platform.links pl
  ON pl.entity_type = 'beneficiary' AND pl.entity_id = b.id
 AND pl.provider = 'morgan-lloyd' AND pl.link_type = 'primary'
WHERE b.account_id = :aid
ORDER BY b.proportion DESC, b.name
"""

_LINK_SQL = """
INSERT INTO platform.links
  (tenant_id, entity_type, entity_id, provider, link_type, external_id)
VALUES (:tenant_id, :kind, :eid, 'morgan-lloyd', :link_type, :ext)
ON CONFLICT (tenant_id, provider, entity_type, entity_id, link_type)
DO UPDATE SET external_id = EXCLUDED.external_id
"""


async def _record_link(
    session: AsyncSession,
    *,
    tenant_id: int,
    kind: str,
    entity_id: int,
    external_id: str,
    link_type: str = LINK_SCHEME,
) -> None:
    """Record the link under the given tenant.

    The tenant is PASSED, not read from `auth.tenant_id()`: `client.sync` runs as a baton action
    under `service_role` with no request JWT, where `auth.tenant_id()` is NULL — and a NULL
    `tenant_id` is the NotNullViolation this replaced. The caller only ever passes the tenant it is
    already scoped to (the run's firm), and RLS still guards a request-context write."""
    await session.execute(
        text(_LINK_SQL),
        {
            "tenant_id": tenant_id,
            "kind": kind,
            "eid": entity_id,
            "ext": external_id,
            "link_type": link_type,
        },
    )


async def _sync_adviser(
    session: AsyncSession, *, tenant_id: int, contact_id: int
) -> tuple[dict[str, Any], str | None]:
    """Ensure this client's adviser exists in Morgan Lloyd.

    Returns ``(report_step, guid)``. The GUID is what ``cam_createcontact`` attaches; it is the
    assigned adviser's when we have one, otherwise the deployment's configured contact.
    """
    fallback = _settings().adviser_contact_id.strip() or None
    row = (await session.execute(text(_ADVISER_SQL), {"cid": contact_id})).mappings().one_or_none()
    if row is None:
        return {"status": "skipped", "external_id": fallback}, fallback

    existing = str(row["ml_adviser_id"] or "").strip() or None
    if existing:
        return {"status": "skipped", "external_id": existing}, existing

    first = str(row["first_name"] or "").strip()
    last = str(row["last_name"] or "").strip()
    if not first or not last:
        return {
            "status": "failed",
            "external_id": fallback,
            "error": "The assigned adviser needs a first name and a last name before they can "
            "be created at Morgan Lloyd.",
        }, fallback

    firm = _settings().adviser_firm_id.strip()
    if not firm:
        return {
            "status": "failed",
            "external_id": fallback,
            "error": "The Morgan Lloyd adviser firm is missing, so the adviser contact cannot "
            "be created.",
        }, fallback

    try:
        guid, minted = await _create_adviser(
            firm_id=firm,
            first_name=first,
            last_name=last,
            email=str(row["email"] or "").strip() or None,
        )
    except (MorganLloydUnavailable, ValueError) as exc:
        logger.warning("morgan lloyd adviser creation failed for contact %s: %s", contact_id, exc)
        return {"status": "failed", "external_id": fallback, "error": str(exc)}, fallback

    await session.execute(
        text(_PROVIDER_REFS_SQL), {"u": row["user_id"], "ext": guid, "tenant_id": tenant_id}
    )
    return {"status": "created" if minted else "skipped", "external_id": guid}, guid


async def _create_adviser(
    *, firm_id: str, first_name: str, last_name: str, email: str | None
) -> tuple[str, bool]:
    """Find or create a Financial Adviser Contact.

    Returns ``(guid, minted)``. ``cam_CreateFinancialAdviserContact`` has no read of its own,
    Dynamics has no delete, and a lost ``provider_refs`` write would otherwise mint a second
    person on the next press. Matching first + last under the firm is what the live suite
    does (`anchors.ensure_adviser`) because that is all ``get_financial_adviser_contacts``
    returns. ``minted`` is false when that lookup already found them.
    """
    from morgan_lloyd.client import MorganLloydClient

    try:
        async with MorganLloydClient(settings=_settings()) as api:
            for person in await api.get_financial_adviser_contacts(firm_id.strip("{}")):
                if (
                    str(person.get("firstname") or "").strip().casefold() == first_name.casefold()
                    and str(person.get("lastname") or "").strip().casefold() == last_name.casefold()
                ):
                    found = str(person.get("contactid") or "").strip()
                    if found:
                        return found.strip("{}"), False
            payload: dict[str, Any] = {
                "accountId": firm_id.strip("{}"),
                "title": _title_code(None),
                "firstName": first_name,
                "lastName": last_name,
            }
            if email:
                payload["emailAddress"] = email
            response = await api.create_financial_adviser_contact(payload)
    except (MorganLloydError, httpx.HTTPError) as exc:
        raise MorganLloydUnavailable(_explain(exc)) from exc

    guid = _action_guid(response, "financialAdviserContactId", "contactid", "contactId")
    if guid is None:
        raise MorganLloydUnavailable(
            f"Morgan Lloyd created the adviser but returned no id (keys: {sorted(response)})."
        )
    return guid, True


async def _sync_beneficiaries(
    session: AsyncSession,
    *,
    tenant_id: int,
    account_id: int,
    scheme_ref: str,
    client: dict[str, Any],
) -> list[dict[str, Any]]:
    """Push this account's nominations, recording each link so a retry does not duplicate."""
    rows = (await session.execute(text(_BENEFICIARIES_SQL), {"aid": account_id})).mappings().all()
    out: list[dict[str, Any]] = []
    for row in rows:
        nomination = dict(row)
        display = " ".join(
            part for part in (nomination.get("title"), nomination.get("name")) if part
        ).strip() or str(nomination.get("name") or "")
        step: dict[str, Any] = {
            "uid": nomination["uid"],
            "name": display,
            "status": "skipped",
        }
        existing = str(nomination.get("ml_beneficiary_id") or "").strip() or None
        if existing:
            step["external_id"] = existing
            out.append(step)
            continue
        try:
            guid = await _create_beneficiary(nomination, scheme_ref=scheme_ref, client=client)
        except (MorganLloydUnavailable, ValueError) as exc:
            logger.warning(
                "morgan lloyd beneficiary creation failed for %s: %s", nomination.get("uid"), exc
            )
            step |= {"status": "failed", "error": str(exc)}
            out.append(step)
            continue
        await _record_link(
            session,
            tenant_id=tenant_id,
            kind="beneficiary",
            entity_id=int(nomination["id"]),
            external_id=guid,
            link_type=LINK_BENEFICIARY,
        )
        step |= {"status": "created", "external_id": guid}
        out.append(step)
    return out


async def _create_beneficiary(
    nomination: dict[str, Any], *, scheme_ref: str, client: dict[str, Any]
) -> str:
    """Nominate one beneficiary on the scheme. Required fields are refused, not invented."""
    name = " ".join(
        part for part in (nomination.get("title"), nomination.get("name")) if part
    ).strip()
    relationship = str(nomination.get("relationship") or "").strip()
    if not name:
        raise ValueError("A beneficiary needs a name before they can be synced.")
    if not relationship:
        raise ValueError(
            f"{name} has no relationship recorded. Morgan Lloyd requires one before the "
            "nomination can be synced."
        )
    address = _beneficiary_address(nomination.get("address"), client)
    missing = [label for label, value in address.items() if not value]
    if missing:
        raise ValueError(
            f"{name} is missing {', '.join(missing)} — Morgan Lloyd requires an address on "
            "the nomination. Add one, or record the member's address on the client."
        )

    from morgan_lloyd.client import MorganLloydClient

    try:
        async with MorganLloydClient(settings=_settings()) as api:
            response = await api.create_beneficiary(
                {
                    "schemeReference": scheme_ref,
                    "cam_nameofbeneficiary": name,
                    "cam_percentageoffund": float(nomination["proportion"]),
                    "cam_nominationdate": _iso_date(nomination.get("nominated_on")),
                    "cam_relationship": relationship,
                    "cam_address_line1": address["line 1"],
                    "cam_city": address["city"],
                    "cam_postcode": address["postcode"],
                }
            )
    except (MorganLloydError, httpx.HTTPError) as exc:
        raise MorganLloydUnavailable(_explain(exc)) from exc

    guid = _action_guid(response, "beneficiaryId", "cam_nominationofbeneficiaryid")
    if guid is None:
        raise MorganLloydUnavailable(
            f"Morgan Lloyd created the beneficiary but returned no id (keys: {sorted(response)})."
        )
    return guid


def _beneficiary_address(recorded: Any, client: dict[str, Any]) -> dict[str, str | None]:
    """Line 1, city, postcode — the nomination's own, else the member's.

    The account panel does not currently collect an address on a nomination, so most rows
    have none. Sending nothing is a 400 naming an object reference; inventing one would put
    a false address on a death-benefit record. The member's address is the one an
    expression-of-wish form typically already holds, so it is the honest fallback.
    """
    parsed = recorded
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except ValueError:
            parsed = None
    if not isinstance(parsed, dict):
        parsed = {}
    return {
        "line 1": _addr(parsed, "line1", "address_line1") or _text(client.get("address_line1")),
        "city": _addr(parsed, "city", "address_city") or _text(client.get("address_city")),
        "postcode": _addr(parsed, "postcode", "postal_code", "address_postcode")
        or _text(client.get("address_postcode")),
    }


def _addr(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        found = value.get(key)
        if isinstance(found, str) and found.strip():
            return found.strip()
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _iso_date(value: Any) -> str:
    if value is None or value == "":
        return date.today().isoformat()
    if hasattr(value, "isoformat"):
        return str(value.isoformat())[:10]
    return str(value)[:10]


def _action_guid(response: dict[str, Any], *keys: str) -> str | None:
    """A GUID out of a custom-action envelope, preferring the named keys.

    Same trap `contacts._created_id` documents: the id is nested under `response` more often
    than it sits at the top level. Named keys first so an adviser id is not read out of a
    neighbouring field; then any GUID in the envelope, which is how a vendor that only echoes
    the id in `message` is still recorded.
    """
    candidates: list[Any] = [response.get(key) for key in keys]
    inner = response.get("response")
    if isinstance(inner, dict):
        candidates.extend(inner.get(key) for key in keys)
        candidates.extend(inner.values())
    elif isinstance(inner, str):
        candidates.append(inner)
    candidates.append(response.get("message"))
    for value in candidates:
        if isinstance(value, str) and (found := _GUID.search(value.strip("{}"))):
            return found.group(0)
    return None


async def sync_client(
    session: AsyncSession,
    *,
    contact_id: int,
    account_id: int | None = None,
    tenant_id: int | None = None,
) -> dict[str, Any]:
    """Create whatever this client is missing in Morgan Lloyd, and record the links.

    Returns a per-step report rather than raising on the first problem: an account whose scheme
    cannot be created should not hide the fact that the contact synced, and an adviser reading
    the result needs to know which half worked. Only a hard precondition (no credentials, no
    client, missing required details) refuses outright.

    ``account_id`` NARROWS THE SECOND HALF, and is what the account record's own push calls. The
    contact is never narrowed away: a scheme is created AGAINST a contact and Morgan Lloyd refuses
    one that does not exist, so pushing an account necessarily pushes the client first — which is
    also why this is one function with a filter rather than a second entry point. Everything that
    makes the whole-client push safe (each step re-reads its link and reports ``skipped``, a
    per-account failure is reported rather than raised, the resume point is the missing link and
    not a cursor) is the part that must not be written twice.

    The adviser is synced BEFORE the contact because ``cam_createcontact`` requires the pair
    ``cam_financialadviser_Account`` + ``cam_FinancialAdviserContact`` together. Beneficiaries
    come AFTER the scheme: ``cam_createbeneficiaries`` is addressed by ``schemeReference``.
    """
    # THE CLIENT FIRST. Checking credentials before the lookup 503'd every unknown id on a
    # machine without MORGAN_LLOYD_* (CI), while a laptop with creds 404'd — so the permission
    # tripwire could not pin "past the gate" without depending on the runner's dotenv. A missing
    # client is not a deployment problem.
    row = (await session.execute(text(_CLIENT_SQL), {"cid": contact_id})).mappings().one_or_none()
    if row is None:
        raise LookupError("Client not found")
    if not configured():
        raise unconfigured("MORGAN_LLOYD_TENANT_ID / _CLIENT_ID / _CLIENT_SECRET")
    client = dict(row)
    # The links are written under the CLIENT'S OWN tenant (carried on the loaded row), not read
    # from `auth.tenant_id()`: a baton action runs as `service_role` with no request JWT, where
    # that function is NULL — the cause of the links NotNullViolation. The client's own tenant also
    # keeps the old guarantee: you cannot sync a client into another firm.
    if tenant_id is None:
        tenant_id = int(client["tenant_id"])

    report: dict[str, Any] = {
        "adviser": {"status": "skipped", "external_id": None},
        "contact": {"status": "skipped", "external_id": client["ml_contact_id"]},
        "accounts": [],
        "warnings": [],
    }

    # The adviser firm is a prerequisite, not a step. Without it the contact is created
    # unattached and every later `cam_createscheme` on it 403s — so say so now, while the person
    # reading it can still act, rather than letting it surface as a permissions error later.
    if not _settings().adviser_firm_id.strip():
        logger.warning("Morgan Lloyd sync: MORGAN_LLOYD_ADVISER_FIRM_ID is unset")
        report["warnings"].append(
            "The Morgan Lloyd adviser firm is missing. Contact support before creating schemes."
        )

    accounts = (
        (
            await session.execute(
                text(_ACCOUNTS_SQL),
                {"cid": contact_id, "custodians": list(custodians())},
            )
        )
        .mappings()
        .all()
    )
    if account_id is not None:
        # NARROWED HERE rather than in the SQL, so an account that is not this client's falls out
        # of the same list every other account is read from — a second WHERE clause would be a
        # second place for "which accounts may be pushed" to be decided.
        #
        # AND REFUSED BEFORE THE CONTACT IS CREATED. Both of these used to raise from inside the
        # loop, which is after step 1 has already put a person in the CRM: a request refused for
        # naming the wrong account would still have left a real record behind, and Morgan Lloyd
        # has no delete.
        accounts = [row for row in accounts if int(row["id"]) == account_id]
        if not accounts:
            raise LookupError("That account is not one of this client's")
        if str(accounts[0]["product_type"] or "").upper() not in PENSION_PRODUCTS:
            # Asked FOR a GIA, silence is the wrong answer: the caller named this account, so the
            # refusal says why. The whole-client push still SKIPS one, because there it is not an
            # ask but a wrapper that simply has no scheme.
            raise ValueError(
                "Morgan Lloyd administers pension schemes, and this account is not one. "
                "Only a SIPP or SSAS can be pushed."
            )
        if (accounts[0].get("custodian") or "") != "sei":
            custodian = accounts[0].get("custodian") or "unset"
            raise ValueError(
                f"Morgan Lloyd administers SEI SIPPs. This account's custodian is {custodian}."
            )

    # Required contact details are refused BEFORE the adviser is minted. Creating a Dynamics
    # adviser for a client we then cannot push would still leave a real person in a system with
    # no delete, and the 422 an adviser reads names the client's missing fields, not that.
    contact_guid = client["ml_contact_id"]
    if not contact_guid:
        absent = missing_fields(client)
        if absent:
            raise ValueError(
                "Morgan Lloyd requires these details before this client can be synced: "
                + ", ".join(absent)
            )

    # ── 1. the adviser ────────────────────────────────────────────────────────────────────
    adviser_step, adviser_guid = await _sync_adviser(
        session, tenant_id=tenant_id, contact_id=contact_id
    )
    report["adviser"] = adviser_step
    if adviser_guid:
        client["ml_adviser_contact_id"] = adviser_guid

    # ── 2. the contact ────────────────────────────────────────────────────────────────────
    if not contact_guid:
        contact_guid = await create_contact(client)
        await _record_link(
            session,
            tenant_id=tenant_id,
            kind="contact",
            entity_id=contact_id,
            external_id=contact_guid,
        )
        report["contact"] = {"status": "created", "external_id": contact_guid}

    # ── 3. a scheme, then a policy, then beneficiaries, per pension account ───────────────
    #
    # TWO CALLS, TWO LINKS, CHECKED SEPARATELY. The scheme is what every scoped action is
    # addressed by; the policy is what a VALUATION is filed against, and it is a distinct vendor
    # call (`cam_createwrapper`) minting a distinct identifier. Doing only the first is what left
    # `services/morgan-lloyd`'s valuation sink returning `skipped` for every scheme on every run,
    # for want of an identifier nothing recorded.
    #
    # Each step re-reads its own link first, so a run that created the scheme and then failed on
    # the policy resumes at the policy rather than creating a second scheme. Beneficiaries need
    # the scheme reference, not the policy, so a failed policy still nominates.
    for account in accounts:
        if not _morgan_lloyd_account(account):
            continue  # a GIA, an ISA, or a Seccl SIPP has no scheme in Morgan Lloyd's model

        row_id = int(account["id"])
        step: dict[str, Any] = {"account_id": row_id, "account_uid": account["uid"]}
        scheme_ref = account["ml_scheme_ref"]

        if scheme_ref:
            step |= {"status": "skipped", "scheme_reference": scheme_ref}
        else:
            try:
                scheme_ref = await _create_scheme(contact_guid, account)
            except (MorganLloydUnavailable, ValueError) as exc:
                # One account's scheme failing must not abandon the others, or the report lies
                # about what state the client is in.
                logger.warning(
                    "morgan lloyd scheme creation failed for account %s: %s", row_id, exc
                )
                report["accounts"].append({**step, "status": "failed", "error": str(exc)})
                continue
            await _record_link(
                session,
                tenant_id=tenant_id,
                kind="account",
                entity_id=row_id,
                external_id=scheme_ref,
                link_type=LINK_SCHEME,
            )
            step |= {"status": "created", "scheme_reference": scheme_ref}

        # ── the policy ────────────────────────────────────────────────────────────────────
        if account["ml_policy_ref"]:
            step["policy_status"] = "skipped"
            step["policy_reference"] = account["ml_policy_ref"]
        else:
            try:
                policy_ref = await _create_policy(str(scheme_ref), account)
            except (MorganLloydUnavailable, ValueError) as exc:
                # REPORTED, NOT RAISED, and the scheme's own status is left standing: a scheme
                # with no policy is a usable record (contributions and transfers address the
                # scheme) that simply cannot be valued yet. Collapsing the two into one "failed"
                # would say the account never reached Morgan Lloyd, which is false.
                logger.warning(
                    "morgan lloyd policy creation failed for account %s: %s", row_id, exc
                )
                step |= {"policy_status": "failed", "policy_error": str(exc)}
            else:
                await _record_link(
                    session,
                    tenant_id=tenant_id,
                    kind="account",
                    entity_id=row_id,
                    external_id=policy_ref,
                    link_type=LINK_POLICY,
                )
                step |= {"policy_status": "created", "policy_reference": policy_ref}

        step["beneficiaries"] = await _sync_beneficiaries(
            session,
            tenant_id=tenant_id,
            account_id=row_id,
            scheme_ref=str(scheme_ref),
            client=client,
        )
        report["accounts"].append(step)

    if not report["accounts"] and account_id is None:
        held_pension = any(
            str(row.get("product_type") or "").upper() in PENSION_PRODUCTS for row in accounts
        )
        if held_pension:
            report["warnings"].append(
                "This client holds no SEI SIPP, so no Morgan Lloyd scheme was created."
            )
        else:
            report["warnings"].append(
                "This client holds no pension account, so no scheme was created. "
                "Drawdown needs one."
            )
    return report


def _configured_int(value: Any, field: str) -> int | None:
    """A configured option-set number: None when absent, an int when readable, else a refusal.

    `product_platforms.data` is jsonb, so a value put there by hand arrives as a string, a number
    or a typo. ABSENT AND UNREADABLE ARE DIFFERENT ANSWERS and must not be collapsed: absent means
    "this product does not say, use the deployment's", where unreadable means somebody DID say and
    got it wrong. Treating a typo as absent would quietly create the scheme under the deployment's
    pairing — a pairing that product's configurer explicitly did not choose — and the vendor
    cannot tell us, because 14 pairings are valid and the wrong valid one succeeds.
    """
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError as exc:
        raise ValueError(
            f"This product's Morgan Lloyd {field} is set to {value!r}, which is not an option-set "
            "number. Correct it on the product — it will not be guessed, and the deployment "
            "default is not a safe substitute for a pairing somebody chose."
        ) from exc


def scheme_types(account: dict[str, Any] | Any) -> tuple[int | None, int | None]:
    """The `schemeType` / `sippType` to create this account's scheme with.

    THE PRODUCT FIRST, THE DEPLOYMENT SECOND. ML's SIPP covers scheme types 16 **and** 17 and the
    `sippType` values are documented with no labels at all, so which pair a given account needs is
    a property of the PRODUCT it was opened under — `platform.product_platforms.data`, the table
    that already exists to hold "this platform's own reference and name for the same shelf item".
    A firm offering two SIPP products cannot express that in one pair of environment variables,
    and `MORGAN_LLOYD_SCHEME_TYPE` is per-deployment: two firms on one deployment would share it.

    The settings stay as the fallback rather than being removed — a firm with a single SIPP has
    nothing to disambiguate, and that is the configuration in place today.

    Both must resolve. A HALF-configured pair is refused by the caller rather than completed from
    the other source, because mixing a product's `schemeType` with the deployment's `sippType`
    would send a pairing nobody chose — and the 14 valid pairings are the vendor's, not ours.

    Raises ``ValueError`` for a value that is present and unreadable; see
    :func:`_configured_int`.
    """
    product_scheme = _configured_int(account.get("product_scheme_type"), "scheme type")
    product_sipp = _configured_int(account.get("product_sipp_type"), "SIPP type")
    if product_scheme is not None or product_sipp is not None:
        return product_scheme, product_sipp
    settings = _settings()
    return settings.scheme_type, settings.sipp_type


async def _create_scheme(contact_guid: str, account: dict[str, Any] | Any) -> str:
    """Create one scheme and return its reference (`MLSIPP/00001`)."""
    scheme_type, sipp_type = scheme_types(account)
    if not scheme_type or not sipp_type:
        # The message names the PRODUCT, which is the half a firm can put right. The deployment
        # fallback is the other half and is ours, so it goes to the log rather than to an adviser
        # who has no access to it.
        logger.warning(
            "Morgan Lloyd scheme refused: no scheme/SIPP type on the product, and "
            "MORGAN_LLOYD_SCHEME_TYPE / MORGAN_LLOYD_SIPP_TYPE are unset"
        )
        raise ValueError(
            "Set the Morgan Lloyd scheme type and SIPP type on this product before creating "
            "the scheme."
        )
    from morgan_lloyd.client import MorganLloydClient

    try:
        async with MorganLloydClient(settings=_settings()) as api:
            response = await api.create_scheme(
                {
                    "contactId": contact_guid,
                    "schemeType": scheme_type,
                    "sippType": sipp_type,
                }
            )
    # THE CLIENT DOES NOT RAISE httpx. `morgan_lloyd._base` wraps every transport and status
    # error into `MorganLloydApiError`, which descends from `MorganLloydError` and NOT from
    # `httpx.HTTPError` — so an `except httpx.HTTPError` here caught nothing and a rejected vendor
    # call, or an Azure Conditional Access block, escaped as an unhandled 500 with a stack trace
    # instead of the 502 this route's docstring promises. httpx stays in the tuple for anything
    # raised outside the client.
    except (MorganLloydError, httpx.HTTPError) as exc:
        raise MorganLloydUnavailable(_explain(exc)) from exc

    reference = _scheme_reference(response)
    if reference is None:
        raise MorganLloydUnavailable(
            "Morgan Lloyd created the scheme, but confirmation could not be saved. Contact support."
        )
    return reference


async def _create_policy(scheme_reference: str, account: dict[str, Any] | Any) -> str:
    """Create the account's POLICY (`cam_createwrapper`) and return its policy number.

    The vendor titles this "Create Investment" and the action is `cam_createwrapper`; there is no
    `cam_createinvestment` in the org. Three required fields, and the third is the interesting
    one:

    * `provider` — "The Firm ID we provided you". One value under three field names (see
      `settings.investment_provider`), so it comes from configuration and is never guessed.
    * `schemeReference` — the `MLSIPP/…` the scheme step just minted. Not the policy number.
    * `policyNumber` — **ours to mint**, and documented only as "the unique policy number for the
      application". So it is the account's own `uid`: already unique, already public (the
      addressing rule — a serial must never leave this estate), already the identifier an
      operator reading either system would use to line the two up. Minting a fresh nanoid here
      instead would create a third identifier for one account, knowable only by reading
      `platform.links`.

    Because the number is ours, the response is a CONFIRMATION rather than the source of the id —
    the vendor "echoes the policy number back as `wrapper`". So the echo is checked when present
    and the sent value is what gets recorded: a vendor that stops echoing must not cost us the
    link to a policy it did create.

    **THE POLICY IS CREATED ASYNCHRONOUSLY, AND THE ANSWER DOES NOT SAY SO.** Roughly two minutes
    on the dev org, during which `cam_policies` has no row for the number and a valuation against
    it is refused as `(missing policy)` — while this action has already answered
    `{"responseKey": 0, "message": "<number> Policy Exists"}`, which it answers either way. That
    lag was read as "the call created nothing" once and cost a rework of this whole step before a
    later look found the policy present. Nothing here waits for it: the link records the number we
    SENT, which is the point of minting it. A caller that creates an account and values it in the
    same breath is the one that has to allow for the delay.
    """
    uid = str(account.get("uid") or "").strip()
    if not uid:
        # 0104 made `platform.accounts.uid` NOT NULL, so this is unreachable through the API and
        # is a guard against a hand-built row rather than an expected path. Refused rather than
        # substituted: a policy number is permanent at the vendor and there is no delete.
        raise ValueError("This account has no uid, so no policy number can be issued for it.")

    settings = _settings()
    provider = settings.investment_provider.strip()
    if not provider:
        # A ValueError and NOT `unconfigured()`: the per-account loop catches
        # `(MorganLloydUnavailable, ValueError)` and reports the failure against this account,
        # where a `MorganLloydNotConfigured` would escape and abandon the whole sync halfway.
        # The setting is logged rather than put in the report, which an adviser reads.
        logger.warning(
            "Morgan Lloyd policy refused, not configured: MORGAN_LLOYD_INVESTMENT_PROVIDER_ID"
        )
        raise ValueError(UNAVAILABLE)
    from morgan_lloyd.client import MorganLloydClient

    try:
        async with MorganLloydClient(settings=settings) as api:
            response = await api.create_investment(
                {
                    "provider": provider.strip("{}"),
                    "schemeReference": scheme_reference,
                    "policyNumber": uid,
                }
            )
    except (MorganLloydError, httpx.HTTPError) as exc:
        raise MorganLloydUnavailable(_explain(exc)) from exc

    echoed = _policy_echo(response)
    if echoed is not None and echoed != uid:
        raise MorganLloydUnavailable(
            f"Morgan Lloyd echoed policy number {echoed!r} for an application sent as {uid!r}. "
            "Recording either would give this account a policy reference it may not have."
        )
    return uid


def _policy_echo(response: dict[str, Any]) -> str | None:
    """The policy number the vendor echoed, if it echoed one.

    Absent is not an error (see :func:`_create_policy`): the number is ours, so the echo only ever
    corroborates. `response` is variously a bare string, a nested dict or absent depending on the
    action, the same envelope shape `contacts._created_id` documents.
    """
    for key in ("wrapper", "policyNumber", "policyno"):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    inner = response.get("response")
    if isinstance(inner, dict):
        for key in ("wrapper", "policyNumber", "policyno"):
            value = inner.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(inner, str) and inner.strip():
        return inner.strip()
    return None


def _scheme_reference(response: dict[str, Any]) -> str | None:
    """The scheme reference out of the action envelope.

    Same trap as `contacts._created_id`: the custom actions answer
    `{"@odata.context", "message", "response"}` rather than a flat body, so the reference can be
    nested. Matched by name and then by shape (`MLSIPP/00001`).
    """
    import re

    pattern = re.compile(r"[A-Z]{2,10}SIPP?/\d+|ML[A-Z]*/\d+", re.I)
    for key in ("schemeReference", "schemeref", "reference"):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    inner = response.get("response")
    if isinstance(inner, dict):
        for value in inner.values():
            if isinstance(value, str) and (found := pattern.search(value)):
                return found.group(0)
    if isinstance(inner, str) and (found := pattern.search(inner)):
        return found.group(0)
    if isinstance(message := response.get("message"), str) and (found := pattern.search(message)):
        return found.group(0)
    return None
