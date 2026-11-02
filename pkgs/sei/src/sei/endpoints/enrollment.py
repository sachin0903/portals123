"""Enrolment — opening a client and/or an account (``/accountservice``, ``/enrollmentstatus``).

One call carries both `clientList[]` and `accountList[]`, so "new client with a new account"
is a single request; an existing client is named by `existingClient` instead. That is the one
respect in which SEI is simpler than Seccl, which needs the client to exist first.

**This client does not build the body.** It is eight levels deep with two arrays and 147
leaves, its required set is conditional on the account classification's universe (Charity ⇒
`charityId`, Estate ⇒ `estateInceptionDate`, Pension ⇒ `pensionId`, Trust ⇒
`trustAgreementDate`), and half a dozen of its ids are firm-configured values only
`sei.endpoints.reference` can supply. A partial model would be a worse lie than a dict: the
caller assembles the payload from the enrolment builder and this module posts it and reads
the status back.

**Enrolment is asynchronous and two-phase.** The POST returns a BPM handle
(`businessKey`, e.g. ``ENR-23904``) and NOT an account number. The account number, the SEI
account/portfolio global ids and the end-client id arrive from
:meth:`EnrollmentApi.enrollment_status` once the process completes. A caller that treats the
POST's 200 as "the account exists" has nothing to record on `platform.links`.
"""

from __future__ import annotations

from typing import Any

from sei._base import BaseClient
from sei.endpoints._shapes import attributes as _attributes


class EnrollmentApi(BaseClient):
    """Initiate an enrolment and follow it to an account number."""

    async def create_enrollment(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Initiate the enrolment business process (``POST /accountservice/accounts``).

        ``attributes`` is ``data.attributes`` — `firmId`, `clientList[]`, `accountList[]` and
        `caseReference`/`usedBy`/`bypassApproval`. `usedBy` is mandatory (code 2196); the cash
        paths default it to ``Graphene``. `mailingLabelName` lives on `individualDetails`,
        not on `newClient` — a sibling placement is accepted by the POST and then failed
        asynchronously as code 2436. Returns the BPM handle; `businessKey` is what
        :meth:`enrollment_status` takes.

        Set `caseReference` to something of ours (the application uid) on every call: SEI has
        no idempotency key, and this is the only handle that ties a second attempt back to the
        first when a response is lost.
        """
        body = await self.request(
            "POST",
            "/accountservice/accounts",
            json={"data": {"type": "string", "attributes": attributes}},
        )
        return _attributes(body)

    async def enrollment_status(
        self, firm_id: int | str, business_process_instance_id: str
    ) -> dict[str, Any]:
        """Where an enrolment got to (``GET /enrollmentstatus/enrollment/{firmId}/{bpmId}``).

        The payload echoes the whole request back and adds `enrollmentData.enrollmentIdentifiers`
        — `primaryEndClientId`, `accounts[].accountNumber`, `globalIdOfAccount`,
        `globalIdOfPortfolios[]` — plus `validationErrors` / `validationWarningList`.

        **`globalIdOfPortfolios` is not the `portfolioId` a cash call takes.** It is
        `{firm}-SWP-{env}-{entity}` (`3108-SWP-I31-59103919`). Receipts and disbursements
        want the trailing entity id (`59103919`, at most nine digits) and reject the global
        id as a bad format. An account can have several (Capital and Income); this is where
        they are first known, and the nightly Accounts report carries the numeric id
        thereafter (it is grained account × portfolio).
        """
        body = await self.request(
            "GET",
            f"/enrollmentstatus/enrollment/{firm_id}/{business_process_instance_id}",
        )
        return _attributes(body)

    async def submit_enrollment(
        self,
        firm_id: int | str,
        *,
        account_number: str | None = None,
        external_reference_id: str | None = None,
        enrollment_activity_id: str | None = None,
        platform_user: str | None = None,
        bypass_approval: bool | None = None,
    ) -> dict[str, Any]:
        """Submit a worksheet parked at *Work In Progress Complete*
        (``POST /submitenrollmentservice/submitEnrollment``).

        `ACCT_STATUS` 9 is that state (8 is *Incomplete*): SEI holds an enrolment there when
        straight-through processing did not qualify. One of account number / external
        reference / enrolment activity id identifies which.
        """
        attributes: dict[str, Any] = {"firmId": firm_id}
        if account_number:
            attributes["accountNumber"] = account_number
        if external_reference_id:
            attributes["externalReferenceId"] = external_reference_id
        if enrollment_activity_id:
            attributes["enrollmentActivityId"] = enrollment_activity_id
        if platform_user:
            attributes["platformUser"] = platform_user
        if bypass_approval is not None:
            attributes["bypassApproval"] = bypass_approval
        body = await self.request(
            "POST",
            "/submitenrollmentservice/submitEnrollment",
            json={"data": {"attributes": attributes}},
        )
        return _attributes(body)

    async def complete_enrichment_by_activity(
        self, activity_id: str, attributes: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Mark enrolment enrichment complete for one activity
        (``PATCH /enrollment/enrollment/completeEnrollmentEnrichmentByActivity/{activityId}``)."""
        body = await self.request(
            "PATCH",
            f"/enrollment/enrollment/completeEnrollmentEnrichmentByActivity/{activity_id}",
            json={"data": {"attributes": attributes or {}}},
        )
        return _attributes(body)

    async def complete_enrichment_by_business_key(
        self, business_process_instance_id: str, attributes: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """The same, addressed by BPM business key (``PATCH
        /completeenrollmentenrichmentbybusinessprocessinstanceid/…/{businessProcessInstanceId}``)."""
        body = await self.request(
            "PATCH",
            "/completeenrollmentenrichmentbybusinessprocessinstanceid"
            f"/completeEnrollmentEnrichmentByBusinessProcessInstanceId/{business_process_instance_id}",
            json={"data": {"attributes": attributes or {}}},
        )
        return _attributes(body)

    async def add_account_owner(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Add an owner to an existing account (``POST /addaccountownerservice/accountowner``).

        This is how a sole account becomes joint, and how an existing client is attached to an
        account someone else opened. `accountOwnershipType` must change too when it does.
        """
        return _attributes(
            await self.request("POST", "/addaccountownerservice/accountowner", json=attributes)
        )
