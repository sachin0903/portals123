"""Activities — following, cancelling and listing an instruction after it is lodged.

Every write in this package answers with an **activity id**, and an activity is SEI's unit of
work: a receipt, a disbursement, a transfer, an enrolment. This module is how the outcome is
learned, and it is the piece that closes the loop `platform.transaction_expectations` was
built for — that table already holds `provider_reference` (the activity id),
`request_payload`, `response_payload` and `error`; nothing has ever re-read the activity, so
an expectation sits at ``instructed`` whether SEI completed it, rejected it or is waiting on
an approval.

**Two code sets, one of which is not in the nightly drop.** `activityStatus` is the coarse
lifecycle and the drop carries it (Reference `TYPE = ACTIVITY_STATUS`, 15 codes).
`activityState` is the fine-grained position (~430 members) and the drop does **not** carry
it — `sei.endpoints.reference.system_codes` is the only lookup. Until it is read, the states
that decide whether money moved are integers: 22 Sent to Payment Processing System, 16
Accepted, 18 Confirmed, 19 Matching Failed, 29 Instruction Rejected by Custodian, 49 Pending
Approval, 51 Approved, 52 Not Approved, 110 Completed, 111 Unable to Complete.
"""

from __future__ import annotations

from typing import Any

from sei._base import BaseClient
from sei.endpoints._shapes import attributes as _attributes
from sei.endpoints._shapes import rows as _rows


class ActivitiesApi(BaseClient):
    """Read an activity's state, list an account's open ones, cancel one."""

    async def activity(self, activity_id: int | str) -> dict[str, Any]:
        """One activity's status and state
        (``GET /activitymanagementservices/activity/{activityId}``).

        Answers a bare object — `activityId`, `activityState`, `activityStateDescription`,
        `activityStatus`, `activityStatusDescription`. The descriptions come back populated,
        so a single activity does not strictly need the code set; a listing does.
        """
        body = await self.request("GET", f"/activitymanagementservices/activity/{activity_id}")
        return _attributes(body)

    async def activities(
        self,
        firm_id: int | str,
        *,
        account_number: str | None = None,
        activity_id: int | str | None = None,
    ) -> list[dict[str, Any]]:
        """One activity, or every OPEN activity on an account
        (``GET /activitymanagement/activities``).

        The account form is the useful one: it is how you see that a withdrawal is sitting at
        *Pending Approval* rather than lost, and it is the only way to find an activity whose
        id we failed to record.
        """
        params: dict[str, Any] = {"firmId": str(firm_id)}
        if account_number:
            params["accountNumber"] = account_number
        if activity_id is not None:
            params["activityId"] = str(activity_id)
        return _rows(await self.request("GET", "/activitymanagement/activities", params=params))

    async def cancel_activity(
        self, activity_id: int | str, *, reason: str | None = None
    ) -> dict[str, Any]:
        """Cancel a pending activity (``PATCH /activitymanagement/activities/{id}/cancel``).

        **This is the only way to stop a standing instruction.** A recurring receipt or
        disbursement created with `processingScheduleDetails.recurring` has no "delete
        mandate" endpoint: the recurrence lives on the activity, and cancelling the activity
        is what ends it. So a mandate we create must have its activity id recorded, or it
        cannot be stopped through the API at all.
        """
        payload = {"data": {"attributes": {"reason": reason}}} if reason else None
        body = await self.request(
            "PATCH", f"/activitymanagement/activities/{activity_id}/cancel", json=payload
        )
        return _attributes(body)

    async def recurring_scheduled_events(
        self,
        firm_id: int | str,
        *,
        account_number: str | None = None,
        client_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Every standing instruction on an account or client
        (``POST /retrieverecurringscheduledeventsservice/recurringScheduledEvents/retrieve``).

        The read side of the mandate story. Note the nightly drop carries the same population
        as `raw.rec_cash_activities` (55 columns: recurrence frequency and interval, payer and
        pay-to profile ids, net/gross amount, next activity date, description/treatment
        codes), so for reporting the drop is the better source. This is for reading back an
        instruction we have just created, before the next drop exists.
        """
        attributes: dict[str, Any] = {"firmId": str(firm_id)}
        if account_number:
            attributes["accountNumber"] = account_number
        if client_id:
            attributes["clientId"] = client_id
        return _rows(
            await self.request(
                "POST",
                "/retrieverecurringscheduledeventsservice/recurringScheduledEvents/retrieve",
                json={"data": {"attributes": attributes}},
            )
        )
