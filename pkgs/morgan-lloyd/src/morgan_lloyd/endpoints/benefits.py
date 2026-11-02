"""Investment, benefit, document, SSO and messaging endpoints."""

from __future__ import annotations

from typing import Any

from morgan_lloyd._base import BaseClient


class BenefitsApi(BaseClient):
    async def create_investment(self, investment: dict[str, Any]) -> dict[str, Any]:
        # `cam_createwrapper`. **THE POLICY IS CREATED ASYNCHRONOUSLY** — about two minutes on
        # the dev org — and the answer is the same either way:
        # `{"wrapper": "<number>", "response": {"responseKey": 0, "message": "<number> Policy
        # Exists"}}`. Until it materialises `cam_policies` has no row for the number and
        # `cam_createpolicyvaluation` refuses it as `(missing policy)`, which reads as "the create
        # did nothing" and once cost a rework of the caller before a second look found the policy
        # present. Anything that creates a wrapper and values it in the same breath must wait.
        """Create an investment application + policy (``POST cam_createwrapper``).

        Required: provider (the Firm ID Morgan Lloyd issued), schemeReference,
        policyNumber (unique per application). The docs' footnote claims "1 of
        either fields is required" but marks all three Required — send all
        three. Response echoes the policy number back as ``wrapper``.
        """
        return await self.call_action("cam_createwrapper", investment)

    async def get_bank_account_transactions(self, scheme_ref: str) -> list[dict[str, Any]]:
        """List Financial Transactions for every bank account on a Scheme
        (``POST cam_getaccounttransactions``).

        Returns the ``financialTransactions`` array: financialTransactionName,
        cashDate, grossAmount and direction. Gotcha: direction is a STRING
        containing the integer (In=1, Out=2), not an int.
        """
        body = await self.call_action("cam_getaccounttransactions", {"schemeRef": scheme_ref})
        transactions = body.get("financialTransactions", [])
        if not isinstance(transactions, list):
            return []
        return [item for item in transactions if isinstance(item, dict)]

    async def get_member_details(self, benefit_application_id: str) -> dict[str, Any]:
        """Retrieve member details for a Benefit Application (``POST
        cam_memberdetails``).

        Returns valuations/fees, the full Dynamics ``contact`` entity, an
        ``illustrationTemplate`` XML (CTC1 message format),
        ``newBenefitApplication`` (populated only when the supplied application
        is live) and ``withdrawals`` — each with withdrawalId (what
        ``cancel_drawdown`` consumes), drawdownOption (DrawdownOption values),
        amount, frequency (payments per annum: Single=0, Annual=1, Half
        Yearly=2, Quarterly=4, Bimonthly=6, Monthly=12, Fortnightly=26,
        Weekly=52), startDate and IsAmendable. Gotcha: the docs table spells
        one fee field ``crystalisationFee`` (sic) while the example JSON uses
        ``crystallisationFee`` — treat the field name verbatim as received.
        """
        return await self.call_action(
            "cam_memberdetails", {"benefitApplicationId": benefit_application_id}
        )

    async def create_benefit_application(self, application: dict[str, Any]) -> dict[str, Any]:
        """Create a benefit application + drawdown SSO link (``POST
        cam_setmemberdetails``).

        Required: contactId, advised (bool). Optional: schemeRef,
        assetValuation, cashValuation, crystallisedValue, illustrationTemplate
        (CTC1 XML). Returns ``benefitApplicationId`` and the SSO link — the
        docs table names it ``ssoURL`` but the example JSON key is ``ssoUrl``;
        it embeds the loginId + ssoToken GUIDs that ``validate_sso`` checks.
        """
        return await self.call_action("cam_setmemberdetails", application)

    async def update_benefit_application_illustration_template(
        self, benefit_application_id: str, illustration_xml: str
    ) -> str | None:
        """Attach a new Illustration XML to a Benefit Application (``POST
        annotations`` — a raw Web API entity create, NOT a custom action).

        Creates an annotation with the fixed subject "Illustration Template"
        bound to the application via ``objectid_cam_benefitsapplication@odata.bind``.
        The API answers ``204 No Content`` with the new annotation GUID in the
        ``OData-EntityId`` header, which is returned here (no JSON body).
        """
        response = await self._request(
            "POST",
            "annotations",
            json={
                "objectid_cam_benefitsapplication@odata.bind": (
                    f"/cam_benefitsapplications({benefit_application_id})"
                ),
                "subject": "Illustration Template",
                "notetext": illustration_xml,
            },
        )
        entity_id = response.headers.get("OData-EntityId")
        return str(entity_id) if entity_id is not None else None

    async def drawdown_setup(self, drawdown: dict[str, Any]) -> dict[str, Any]:
        """Process a client's drawdown (``POST cam_drawdownsetup``).

        Required: benefitApplicationId, drawdownOption (DrawdownOption),
        phasedUFPLS, phasedPCLS, investmentPathway (1-6), sortCode,
        accountNumber, accountName, tfcUsed. Optional amounts +
        incomeFrequency (IncomeFrequency) and paymentDay (6, 14, 21 or 28).
        Gotchas: the guidance/advice appointment Yes/No fields are STRINGS
        "0"/"1", not booleans, and the response envelope nests
        ``responseMessage`` instead of ``message``.
        """
        return await self.call_action("cam_drawdownsetup", drawdown)

    async def get_benefit_events(
        self,
        scheme_reference: str,
        contact_id: str,
        *,
        event_status: int | None = None,
        exclude_ad_hoc: bool | None = None,
    ) -> list[dict[str, Any]]:
        """List a client's Benefit Events (``POST cam_schemememberevents``).

        event_status filters by EventStatus (null = no filter);
        exclude_ad_hoc drops Ad-Hoc events. Returns the ``schemeMemberEvents``
        array: schemeMemberEventId, eventType (EventType), eventStatus
        (EventStatus), benefitsType (BenefitsType), paymentFrequency
        (PaymentFrequency), amounts and dates. Gotcha: ``schemeMemberId``
        comes back as an entity-reference object (GUID + ``__DisplayName__``),
        not the plain string the docs table claims.
        """
        body: dict[str, Any] = {
            "schemeReference": scheme_reference,
            "contactId": contact_id,
            "eventStatus": event_status,
        }
        if exclude_ad_hoc is not None:
            body["excludeAdHoc"] = exclude_ad_hoc
        parsed = await self.call_action("cam_schemememberevents", body)
        events = parsed.get("schemeMemberEvents", [])
        if not isinstance(events, list):
            return []
        return [item for item in events if isinstance(item, dict)]

    async def cancel_drawdown(
        self,
        *,
        scheme_member_event_id: str | None = None,
        policy_number: str | None = None,
        withdrawal_type: int | None = None,
        withdrawal_id: int | None = None,
    ) -> dict[str, Any]:
        """Cancel a Drawdown Event — sets it to Stopped (``POST
        cam_canceldrawdown``).

        Address the event EITHER by scheme_member_event_id alone OR by the
        trio policy_number + withdrawal_type + withdrawal_id together (the
        docs' XOR footnote); anything else raises ValueError. withdrawal_type
        takes WithdrawalType values (the same option set as DrawdownOption);
        withdrawal_id comes from ``get_member_details``'s withdrawals array.
        Gotchas: policy_number is documented as the GUID of the Benefit
        Application record, not a policy-number string, and the event must be
        Live with a BCE Frequency that is not Ad-Hoc.
        """
        trio = (policy_number, withdrawal_type, withdrawal_id)
        if scheme_member_event_id is not None:
            if any(value is not None for value in trio):
                raise ValueError(
                    "supply scheme_member_event_id alone, or the "
                    "policy_number/withdrawal_type/withdrawal_id trio - not both"
                )
            body: dict[str, Any] = {"schemeMemberEventId": scheme_member_event_id}
        else:
            if any(value is None for value in trio):
                raise ValueError(
                    "supply scheme_member_event_id, or ALL of "
                    "policy_number, withdrawal_type and withdrawal_id"
                )
            body = {
                "policyNumber": policy_number,
                "withdrawalType": withdrawal_type,
                "withdrawalId": withdrawal_id,
            }
        return await self.call_action("cam_canceldrawdown", body)

    async def update_mvp_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        """Insert a new My Viewpoint Profile for a contact (``POST
        cam_updatemvpprofile`` — the action name is case sensitive).

        Required: contactid (lowercase, the CRM Contact GUID). Option sets:
        cam_title (Title), cam_legalsex (Male=1/Female=2),
        cam_familystatuscode, cam_ownershipstatus; dates are yyyy-MM-dd
        strings. Gotcha: the docs' example body carries stray trailing spaces
        in some keys ("cam_familystatuscode ", "cam_ninumber ") and a
        ``cam_preferrednName`` typo — use the clean field names. Flat
        envelope; success message "Information Received".
        """
        return await self.call_action("cam_updatemvpprofile", profile)

    async def submit_document(
        self,
        document: str,
        subject: str,
        *,
        document_type: int,
        document_folder: int,
        contact_id: str | None = None,
        investor_number: str | None = None,
        product_type: str | None = None,
        document_ref: str | None = None,
    ) -> dict[str, Any]:
        """Submit a document by Base64 payload or URL (``POST
        cam_submitdocument``).

        document_type is DocumentType (Base64 encode=1, URL=2) and
        document_folder is DocumentFolder. One or both of contact_id /
        investor_number are required (ValueError if neither). product_type is
        only relevant to the Illustration folder (e.g. "MLI"). The document is
        created as a .pdf by default — add a file extension to subject for a
        different type. Gotcha: the response envelope nests
        ``responseMessage`` instead of ``message``.
        """
        if contact_id is None and investor_number is None:
            raise ValueError("supply contact_id and/or investor_number")
        body: dict[str, Any] = {
            "document": document,
            "subject": subject,
            "documentType": document_type,
            "documentFolder": document_folder,
        }
        if contact_id is not None:
            body["contactId"] = contact_id
        if investor_number is not None:
            body["investorNumber"] = investor_number
        if product_type is not None:
            body["productType"] = product_type
        if document_ref is not None:
            body["documentRef"] = document_ref
        return await self.call_action("cam_submitdocument", body)

    async def submit_illustration(
        self,
        illustration_url: str,
        *,
        contact_id: str | None = None,
        investor_number: str | None = None,
    ) -> dict[str, Any]:
        """Submit an illustration PDF from a URL (``POST cam_illustration``).

        One or both of contact_id / investor_number are required (ValueError
        if neither). Flat envelope (top-level ``responseKey``/``message``).
        """
        if contact_id is None and investor_number is None:
            raise ValueError("supply contact_id and/or investor_number")
        body: dict[str, Any] = {"illustrationUrl": illustration_url}
        if contact_id is not None:
            body["contactId"] = contact_id
        if investor_number is not None:
            body["investorNumber"] = investor_number
        return await self.call_action("cam_illustration", body)

    async def validate_sso(self, login_id: str, sso_token: str) -> dict[str, Any]:
        """Validate a member's single sign-on (``POST cam_validatesso``).

        login_id and sso_token are the GUIDs embedded in the ``ssoUrl``
        returned by ``create_benefit_application``. Flat envelope.
        """
        return await self.call_action(
            "cam_validatesso", {"loginId": login_id, "ssoToken": sso_token}
        )

    async def create_thread(self, thread: dict[str, Any]) -> dict[str, Any]:
        """Create a message Thread (``POST cam_createthread``).

        Required: cam_threadref (your system's unique thread id — it becomes
        the new entity's ``cam_threadid``), cam_contactref, cam_subject.
        Returns ``threadEntity`` (an entity reference for the new Thread)
        plus the envelope.
        """
        return await self.call_action("cam_createthread", thread)

    async def create_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """Create a Message on a thread (``POST cam_createmessage``).

        Required: cam_threadref, cam_messagetype (MessageType — documented as
        a string but takes integer option-set values, e.g. 0), cam_text.
        Optional: cam_flag (None=0, Read=1, Deleted=2, Viewed=3),
        cam_attachmentflag (Not Read=0, Read=1), cam_documentref. Returns
        ``messageEntity`` (entity reference keyed by ``activityid``).
        """
        return await self.call_action("cam_createmessage", message)

    async def update_message(
        self,
        message_id: str,
        *,
        flag: int | None = None,
        attachment_flag: int | None = None,
    ) -> dict[str, Any]:
        """Update a Message's flags (``POST cam_updatemessage``).

        flag maps to cam_flag (None=0, Read=1, Deleted=2, Viewed=3) and
        attachment_flag to cam_attachmentflag (Not Read=0, Read=1). Flat
        envelope (top-level ``responseKey``/``message``).
        """
        body: dict[str, Any] = {"cam_messageid": message_id}
        if flag is not None:
            body["cam_flag"] = flag
        if attachment_flag is not None:
            body["cam_attachmentflag"] = attachment_flag
        return await self.call_action("cam_updatemessage", body)
