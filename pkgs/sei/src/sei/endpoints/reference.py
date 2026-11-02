"""Reference-data reads — the firm-configured ids every write needs and cannot derive.

**Why this module exists at all**, given the nightly SFTP drop already carries a
183-code-set Reference report (`services/sei`): almost everything is in the drop, and
where it is, the drop is the better source — it is free, complete and already conformed.
Three things are not, and this is the only door to them:

* **Enrolment templates.** `clientTemplateId` and `accountTemplateId` are REQUIRED by
  `POST /accountservice/accounts` and appear in no report and under no Reference `TYPE`.
  Without them an account cannot be opened at all, which makes these two reads the
  narrowest possible unblock for the whole enrolment path.
* **The activity STATE vocabulary.** The drop carries `ACTIVITY_STATUS` (15 codes) and not
  the ~430-member state set, so a lodged receipt's `activityState` is an integer with no
  meaning until this is read.
* **The DSR catalogue.** `raw.client_account_link` carries the grants that EXIST on
  accounts; it cannot carry the pairs a firm may grant and has not used yet. That is what
  a new account needs.

Everything here is a read. Nothing in this module changes anything at SEI.
"""

from __future__ import annotations

import json
from typing import Any

from sei._base import BaseClient
from sei.endpoints._shapes import rows as _rows


def system_code_rows(body: Any) -> list[dict[str, Any]]:
    """The code rows inside ``data.result.SYSTEM_CODES_CACHE``.

    The cache is a list of code objects in the spec's own example, and an empty string on i31
    when the named set is not loaded. ``rows()`` cannot see a list nested under ``result``, so
    it returns the envelope — ``{result, status}`` — as one row, and a caller looking for a
    description finds none. An empty cache is an empty list.
    """
    data = body.get("data") if isinstance(body, dict) else None
    result = data.get("result") if isinstance(data, dict) else None
    cache = result.get("SYSTEM_CODES_CACHE") if isinstance(result, dict) else None
    if isinstance(cache, str):
        cache = cache.strip()
        if not cache:
            return []
        try:
            cache = json.loads(cache)
        except json.JSONDecodeError:
            return []
    if isinstance(cache, list):
        return [row for row in cache if isinstance(row, dict)]
    return []


class ReferenceApi(BaseClient):
    """Firm-configured reference data: enrolment templates, DSR catalogue, code sets."""

    # --- enrolment templates ----------------------------------------------------

    async def client_templates(self, firm_id: int | str) -> list[dict[str, Any]]:
        """Client-level enrolment templates (``GET /enrollmentclienttemplates/…``).

        One of these ids is `clientList[].newClient.clientTemplateId`. A template carries the
        firm's client-side defaults (tax forms, delivery preferences), which is why the
        enrolment body does not have to restate them.
        """
        body = await self.request(
            "GET",
            "/enrollmentclienttemplates/crm/client/templates",
            params={"firmId": str(firm_id)},
        )
        return _rows(body)

    async def account_templates(self, firm_id: int | str) -> list[dict[str, Any]]:
        """Account-level enrolment templates (``GET /enrollmentaccounttemplates/…``).

        The merged spec spells the proxy ``enrollmentAccountTemplates``. Apigee has no proxy
        under that casing — ``404 Unable to identify proxy`` — and does under the
        all-lowercase name, which is how ``enrollmentclienttemplates`` is already spelled.
        The rest of the path keeps the spec's casing (``trustForms``, not ``trustforms``).

        One of these ids is `accountList[].accountTemplateId`, and it is **per shelf
        product, not per wrapper type** — a firm running an "AIM ISA" and an "AIM IHT" has a
        template for each and both are GIAs/ISAs at the wrapper level. That is why the id
        belongs on `platform.product_platforms.account_open_reference`, keyed by product, and
        not in a map keyed by `platform.product_type`.

        **All-lowercase, not the Swagger spelling.** `swagger-spec-EnrollmentAccountTemplates.yaml`
        concatenates `servers.url` as ``/enrollmentAccountTemplates`` (camelCase `A`). That path
        404s on i31 (`Unable to identify proxy for host: sei-default`). The sibling client
        service is documented — and deployed — as ``/enrollmentclienttemplates``, and that is
        the proxy name Apigee actually has for both. Same class of bug as
        ``getAccountDetailsinfo``: the gateway's spelling is the one that exists.
        `tests/test_endpoint_paths.py` refuses the camelCase path so it cannot come back.
        """
        body = await self.request(
            "GET",
            "/enrollmentaccounttemplates/crm/account/templates",
            params={"firmId": str(firm_id)},
        )
        return _rows(body)

    async def account_template_trust_forms(self, firm_id: int | str) -> list[dict[str, Any]]:
        """Which trust tax forms each account template carries
        (``GET /enrollmentaccounttemplates/crm/account/templates/trustForms``).

        Read this before opening a trust account: the template's forms are what make
        `typeOfTrustId` conditionally mandatory, and the vendor's answer to omitting it names
        nothing useful.
        """
        body = await self.request(
            "GET",
            "/enrollmentaccounttemplates/crm/account/templates/trustForms",
            params={"firmId": str(firm_id)},
        )
        return _rows(body)

    # --- DSR (direct servicing relationships) -----------------------------------

    async def firm_dsrs(self, firm_id: int | str) -> dict[str, Any]:
        """The firm's whole DSR catalogue (``POST /getfirmdsrservice/getFirmDsrs``) —
        employees and teams, for both client- and account-level relationship types.

        This is the same population as the `DSR Tags` sheet in SEI's static-reference
        workbook. The tag id it returns is the API's ``servicingRelationshipId``, and the
        client/account split is why one role has two ids: sending the client-level id in an
        account list is refused in a way that reads as "the tag does not exist".
        """
        return dict(
            await self.request(
                "POST",
                "/getfirmdsrservice/getFirmDsrs",
                json={"data": {"attributes": {"firmId": str(firm_id)}}},
            )
        )

    async def eligible_account_team_dsrs(
        self, firm_id: int | str, account_number: str
    ) -> list[dict[str, Any]]:
        """Team DSRs this account may be granted (``GET /eligibleaccountteamdsr/…``)."""
        body = await self.request(
            "GET",
            "/eligibleaccountteamdsr/crm/account/teamdsr",
            params={"firmId": str(firm_id), "accountNumber": account_number},
        )
        return _rows(body)

    async def eligible_account_employee_dsrs(
        self, firm_id: int | str, account_number: str
    ) -> list[dict[str, Any]]:
        """Employee DSRs this account may be granted (``GET /eligibleaccountemployeedsr/…``)."""
        body = await self.request(
            "GET",
            "/eligibleaccountemployeedsr/crm/account/employeedsr",
            params={"firmId": str(firm_id), "accountNumber": account_number},
        )
        return _rows(body)

    async def eligible_client_employee_dsrs(
        self, firm_id: int | str, client_id: str
    ) -> list[dict[str, Any]]:
        """Employee DSRs this client may be granted (``GET /eligibleclientemployeedsr/…``)."""
        body = await self.request(
            "GET",
            "/eligibleclientemployeedsr/crm/client/employeedsr",
            params={"firmId": str(firm_id), "clientId": client_id},
        )
        return _rows(body)

    # --- platform code sets -----------------------------------------------------

    async def heartbeat(self) -> bool:
        """Is SWP answering us at all (``GET /heartbeatapiproxy/status``)?

        **The only call in the whole API that needs no entitlement and no firm**, which makes it
        the correct reachability probe and the only honest way to separate SEI's three failure
        modes:

        * a TLS/network refusal      -> the client certificate or the IP allowlist
        * this returns 200           -> the machine and all three credentials are good
        * a resource 401s afterwards -> `SeiEntitlementError`, a gap in the AppKey's product

        Without it, a suite that probes some ordinary resource cannot tell "SEI will not talk to
        this machine" from "SEI will not serve THIS endpoint to this AppKey" — and the integration
        suite spent a session blaming the VPN for the second one while the token endpoint was
        answering 200. It is what `services/monitoring` has always used.
        """
        body = await self.request("GET", "/heartbeatapiproxy/status")
        return "OK" in str(body.get("Status") or body.get("value") or body)

    async def system_codes(self, code_name: str, *, locale_id: int = 1) -> list[dict[str, Any]]:
        """One platform code set by name (``GET /cachereadservices/system-codes-cache``).

        Use this ONLY for a set the nightly drop's Reference report does not carry — in
        practice the activity **state** vocabulary, which is what turns
        ``activityState: 22`` into "Sent to Payment Processing System". For anything the drop
        has, read the drop: it is already conformed as ``ref.codes`` and costs no request.
        """
        body = await self.request(
            "GET",
            "/cachereadservices/system-codes-cache",
            params={"codeName": code_name, "localeId": str(locale_id)},
        )
        return system_code_rows(body)

    async def firm_default_attributes(self, firm_id: int | str) -> dict[str, Any]:
        """The firm's own defaults (``GET /cachereadservices/firm-default-attr-cache``) —
        what SEI will fill in when the enrolment body leaves a field out."""
        return dict(
            await self.request(
                "GET",
                "/cachereadservices/firm-default-attr-cache",
                params={"firmId": str(firm_id)},
            )
        )

    # --- investment models / strategies -----------------------------------------

    async def firm_employee_users(
        self,
        *,
        processing_org_id: int | str | None = None,
        service_id: int | str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """The firm's employee users (``GET /employeeservice/firmemployeeusers``).

        **The option list for mapping a Graphene login to the SEI employee it is, and the reason
        this is the API rather than the drop.** Enrolment takes
        ``employeeServicingRelationshipList[].employeeEntityId``, and the nightly ``UserDetail``
        report is a poor source for choosing one: twelve columns, no email at all, and a single
        ``USER_DISPLAY_NAME`` holding ``"Armasoiu, Maria"``. This returns ``uniqueUserId``,
        ``emailId``, ``firstName``/``middleName``/``lastName`` separately, ``phone``,
        ``loginUserName``, ``crdNumber`` and ``state``.

        The email is what makes the mapping tractable. Matching on a display NAME would be wrong
        most of the time — only 37 of the 233 distinct ``ACCOUNT_DSR_EMP_ID`` values in
        ClientAccountLink appear in ``UserDetail`` at all, the rest being adviser parties rather
        than SEI logins. An email is an identity, so an exact match on one (and exactly one, the
        bar ``aml.match_contact`` sets for a person) is a defensible proposal for an operator to
        accept. A miss is ordinary and not an error: a firm's SEI address and its Graphene login
        address are frequently different.

        **BOTH SCOPES ARE REQUIRED ON i31**, despite the vendor's own spec marking them
        ``required: false``. An unscoped GET 400s ``processingOrgId`` (code 100014, "Prcossesing
        Organization Id is required"); firm-only 400s ``serviceId`` (100015). Passing neither
        enumerates nothing — it fails. An absent scope is still OMITTED from the query rather
        than sent as an empty string, because a blank is a value SEI matches on; the pipeline
        must supply both.

        ``processing_org_id`` is the **processing organisation**, not the firm. Firm 3102's
        processing org is 3100 ("IWA Market 7") on
        ``GET /cachereadservices/firm-default-attr-cache``. Sending the firm id is HTTP 200
        with zero rows. ``service_id`` is a product-service filter, not a DSR
        ``servicingRelationshipId`` (those 400 as "Service Id is Invalid"). Service 1 is a
        superset of every other populated id in 1–250; the spec's example 152 is empty.

        Every row still carries its own ``firmId``, so one processing-org sweep is attributable
        per firm.

        Paged, because a firm can have hundreds: the response's ``metaData`` carries
        ``totalRecords``, and this returns one page. The picker caps at 50 either way.
        """
        params = {"pageNumber": str(page), "pageSize": str(max(1, min(page_size, 100)))}
        if processing_org_id is not None:
            params["processingOrgId"] = str(processing_org_id)
        if service_id is not None:
            params["serviceId"] = str(service_id)
        body = await self.request(
            "GET",
            "/employeeservice/firmemployeeusers",
            params=params,
        )
        # NAMED EXPLICITLY, not read through `_rows`, and this is the one response in the client
        # where that matters. `_rows` falls back to "the first list-of-objects in the body", and
        # this body has TWO — `employeeUsersForFirmList` and `metaData` (the paging block, itself a
        # list of one object). So it returns whichever appears first in the JSON, and it returns
        # `metaData` outright if the employee list holds any non-dict entry, because its
        # all-dicts test then rejects the real collection and falls through.
        #
        # Measured both ways. The failure is a picker offering "pageNumber 1, pageSize 100,
        # totalRecords 428" as a person to map an adviser onto — which is worse than an empty list,
        # because an empty list reads as "not configured" and this reads as an answer.
        found = body.get("employeeUsersForFirmList") if isinstance(body, dict) else None
        return [row for row in (found or []) if isinstance(row, dict)]

    async def strategies(self, firm_id: int | str) -> list[dict[str, Any]]:
        """Strategies available at the firm (``POST /retrievestrategylistservice/getStrategyList``).

        `MODEL_TYPE` 5 in SEI's vocabulary. The investment models a firm actually assigns
        (`MODEL_TYPE` 8) come from the drop's `raw.models_int_ext`, which also carries the
        `ALLOWABLE_PROCESSING_SCH_ID` the assignment call wants as ``apsId``.
        """
        body = await self.request(
            "POST",
            "/retrievestrategylistservice/getStrategyList",
            json={"data": {"attributes": {"firmId": str(firm_id)}}},
        )
        return _rows(body)
