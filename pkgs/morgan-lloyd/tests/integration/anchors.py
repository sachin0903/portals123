"""Find-or-create anchors: one adviser, one client, one scheme, one policy — reused for ever.

**Dynamics has no delete**, so a lifecycle suite that created a fresh contact and scheme on every
run would leave a permanent trail through a real firm's sandbox. Every anchor here is therefore
DISCOVERED first and created only when the discovery comes back empty. A first run makes four
records; every run after it makes none.

Discovery is from the ORG, never from local state — no cache file, no committed id. A committed id
rots the moment somebody resets the sandbox, and a cache file makes a fresh clone behave
differently from a warm one, which is the property least wanted in a test that already depends on
somebody else's environment.

The chain each anchor is found by:

* **adviser**   `cam_CreateFinancialAdviserContact` has no read of its own, so the adviser is
                found in `contacts` under the firm (`_parentcustomerid_value`) by its marker name.
* **client**    `cam_externalclientid` — the vendor's own "External Client Identifier to be used
                by select third parties", which `populate_test_data.py` already uses for its
                `GRAPHENE-TEST-000n` rows. The integration anchor takes a distinct marker so the
                two populations never collide.
* **scheme**    `_cam_individualclient_value` on `cam_schemes` — the member link. Established by
                reading a scheme's own properties; there is no `_cam_contact_value`, which is what
                the obvious guess would be and what a first attempt tried.
* **policy**    the policy number is OURS to mint (`cam_createwrapper` documents it only as
                "unique per application"), so it is DERIVED from the scheme reference and needs no
                lookup — the same scheme always yields the same policy number. It is READ BACK
                here all the same, because the create is ASYNCHRONOUS (`policy_for_scheme`).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from morgan_lloyd.client import MorganLloydClient

#: Resolved anchor ids, for the life of the process.
#:
#: The discovery is a handful of reads and the anchors do not change during a run, so paying for
#: them once is worth a cache — but the cache holds STRINGS, never a client or a fixture. That is
#: what lets the anchors be function-scoped: a session-scoped async fixture would hold an
#: `asyncio.Event` bound to one event loop, and pytest-asyncio gives each test its own.
_CACHE: dict[str, str] = {}


def cached(key: str) -> str | None:
    return _CACHE.get(key)


def remember(key: str, value: str) -> str:
    _CACHE[key] = value
    return value


#: The client anchor's `cam_externalclientid`. Deliberately not `GRAPHENE-TEST-000n`: that series
#: belongs to `populate_test_data.py` and is regenerated in bulk, so sharing it would let a
#: populate run and a test run fight over the same record.
CLIENT_MARKER = "GRAPHENE-INTEGRATION"

#: The adviser anchor's name. A person's name is the only thing
#: `get_financial_adviser_contacts` returns to match on.
ADVISER_FIRST = "ZZIntegration"
ADVISER_LAST = "Adviser"

#: A NINO ML's validator accepts. **`QQ123456C` — the canonical HMRC example — is REFUSED**
#: (`0x80040224 The NI number QQ123456C, does not conform to the UK standard`), which is worth
#: knowing because it is the number every fixture in this repo reaches for. The `TN…A` shape is
#: what `populate_test_data.py` already uses and what the 20 GRAPHENE-TEST contacts carry.
ANCHOR_NINO = "TN000001A"


async def policy_exists(api: MorganLloydClient, policy_number: str) -> bool:
    """Has Dynamics materialised this policy yet?

    The wrapper create is asynchronous, so this is the only honest way to ask. Its absence means
    "not yet", NOT "the create failed" — the two are indistinguishable from the action's answer,
    and reading the first as the second is what sent a whole design down the wrong path.
    """
    rows = await api.get_collection(
        "cam_policies",
        params={
            "$select": "cam_policyno",
            "$filter": f"cam_policyno eq '{policy_number}'",
            "$top": "1",
        },
    )
    return bool(rows)


def policy_number_for(scheme_reference: str) -> str:
    """The policy number for a scheme's anchor wrapper.

    Derived, not looked up and not random: `cam_createwrapper` documents `policyNumber` only as
    "the unique policy number for the application", which makes it ours to choose — so choosing a
    function of the scheme reference means the same scheme always resolves to the same policy and
    a re-run needs no discovery step at all.

    **The policy it names does not exist the moment the wrapper is created.** Dynamics makes it
    asynchronously — about two minutes on this org — and until then `cam_policies` has no row and
    a valuation is refused as `(missing policy)`, while `cam_createwrapper` has already answered
    `{"responseKey": 0, "message": "<number> Policy Exists"}`, which it answers either way. Read
    that empty window as "the create did nothing" and the conclusion is that the policy number is
    not ours to mint; it is, and a look two minutes later is what says so. `policy_exists` is
    the check that does not race.
    """
    return f"ZZINT-{scheme_reference.replace('/', '-')}"


async def find_adviser(api: MorganLloydClient, firm_id: str) -> str | None:
    """The adviser anchor's contact GUID, if it is already there."""
    for row in await api.get_financial_adviser_contacts(firm_id.strip("{}")):
        if (
            str(row.get("firstname") or "").strip() == ADVISER_FIRST
            and str(row.get("lastname") or "").strip() == ADVISER_LAST
        ):
            return str(row["contactid"])
    return None


async def ensure_adviser(api: MorganLloydClient, firm_id: str) -> str:
    """The adviser anchor, created once.

    `cam_CreateFinancialAdviserContact` is MIXED CASE, unlike every other action here — Dynamics
    action names are case sensitive and this one is spelled differently from its siblings.
    """
    found = await find_adviser(api, firm_id)
    if found:
        return found
    answer = await api.create_financial_adviser_contact(
        {
            "accountId": firm_id.strip("{}"),
            "title": 809130012,  # Other — the anchor is not a real person
            "firstName": ADVISER_FIRST,
            "lastName": ADVISER_LAST,
        }
    )
    created = answer.get("financialAdviserContactId")
    assert isinstance(created, str) and created, (  # noqa: S101 - anchor contract
        f"created an adviser and could not read its id out of {sorted(answer)}"
    )
    return created


async def find_client(api: MorganLloydClient) -> dict[str, Any] | None:
    """The client anchor, by the vendor's own external-id field."""
    rows = await api.get_collection(
        "contacts",
        params={
            "$select": "contactid,firstname,lastname,cam_externalclientid,"
            "_cam_financialadviser_value",
            "$filter": f"cam_externalclientid eq '{CLIENT_MARKER}'",
            "$top": "1",
        },
    )
    return rows[0] if rows else None


def client_payload(firm_id: str, adviser_contact_id: str) -> dict[str, Any]:
    """The anchor client's `cam_createcontact` body.

    A fixed date of birth and a fixed NI number, so a re-created anchor after a sandbox reset is
    the same person rather than a new one. The email is `.invalid`, a reserved TLD that can never
    resolve — nothing here should ever be able to receive mail.
    """
    return {
        "as_contactstatus": 809130002,
        "cam_title": 809130012,
        "firstname": "ZZIntegration",
        "lastname": "Client",
        "emailaddress1": "zzintegration.client@example.invalid",
        "cam_nationality": "GB",
        "gendercode": 1,
        "cam_ninumber": ANCHOR_NINO,
        "address1_line1": "1 Integration Way",
        "address1_city": "Bristol",
        "address1_postalcode": "BS1 4XE",
        "cam_birthdate": "1970-01-01",
        "cam_potentiallyvulnerable": "False",
        "cam_externalclientid": CLIENT_MARKER,
        # THE PAIR IS REQUIRED TOGETHER — attaching the firm without an adviser CONTACT is
        # refused outright (`400 0x80040224 Financial Adviser contact must be populated for the
        # adviser firm`), and both are entity REFERENCES rather than bare GUIDs.
        "cam_financialadviser_Account": f"/accounts({firm_id.strip('{}')})",
        "cam_FinancialAdviserContact": f"/contacts({adviser_contact_id.strip('{}')})",
    }


async def find_scheme(api: MorganLloydClient, contact_id: str) -> str | None:
    """The anchor client's newest scheme reference, by the MEMBER link.

    `_cam_individualclient_value`, NOT `_cam_contact_value` — the latter is the obvious guess and
    does not exist on `cam_scheme` (`400 Could not find a property named '_cam_contact_value'`).
    """
    rows = await api.get_collection(
        "cam_schemes",
        params={
            "$select": "cam_name,createdon",
            "$filter": f"_cam_individualclient_value eq {contact_id.strip('{}')}",
            "$orderby": "createdon desc",
            "$top": "1",
        },
    )
    return str(rows[0]["cam_name"]) if rows else None


async def find_contribution_application(
    api: MorganLloydClient, scheme_reference: str
) -> dict[str, Any] | None:
    """An existing contribution still AT Application on this scheme.

    Reused rather than created, because only an Application can be amended or stopped — so a run
    that finds one exercises the amend/stop path without adding to the pile, and only the first
    run has to make one.
    """
    for application in await api.get_pension_contribution_applications(scheme_reference):
        if int(application.get("cam_status") or 0) == 809130000:  # Application
            return application
    return None


def today() -> str:
    return dt.date.today().isoformat()
