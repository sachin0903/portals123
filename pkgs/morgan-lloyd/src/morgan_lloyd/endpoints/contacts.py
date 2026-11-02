"""Contacts endpoints."""

from __future__ import annotations

import json
from typing import Any

from morgan_lloyd._base import BaseClient


class ContactsApi(BaseClient):
    async def create_contact(self, contact: dict[str, Any]) -> dict[str, Any]:
        """Create a Contact (``POST cam_createcontact``).

        Required: as_contactstatus (ContactStatus), cam_title (Title), firstname,
        lastname, emailaddress1, cam_nationality, gendercode (Gender), cam_ninumber,
        address1_line1, address1_city, address1_postalcode, cam_birthdate.
        cam_potentiallyvulnerable is the string "True"/"False"; when "True" the
        cam_contactvulnerability/cam_vulnerabilityaddedby/... fields become required
        (cam_contactvulnerability is a comma-separated multiselect string — see
        ``morgan_lloyd.enums.multiselect``).
        """
        return await self.call_action("cam_createcontact", contact)

    async def get_all_contacts(self) -> list[dict[str, Any]]:
        """List every Contact (``GET contacts``), following OData pagination.

        Lookup fields come back as ``_<name>_value`` GUID strings; option-set
        fields reuse the Create Contact enums (Title, Gender, FamilyStatus, ...).
        """
        return await self.get_collection("contacts")

    async def get_adviser_contacts(self, adviser_email_address: str) -> list[dict[str, Any]]:
        """List an Adviser's Contacts (``POST cam_getadvisercontacts``).

        Returns the ``contacts`` array. Records are sparse (fields absent when
        empty); cam_potentiallyvulnerable comes back as a real bool here and
        cam_contactvulnerability as a comma-separated multiselect string. The
        response Title enum carries an extra value (Major) absent from Create
        Contact; see also PreferredContactMethod and AnnualReviewFrequency.
        """
        body = await self.call_action(
            "cam_getadvisercontacts", {"adviserEmailAddress": adviser_email_address}
        )
        contacts = body.get("contacts", [])
        if not isinstance(contacts, list):
            return []
        return [item for item in contacts if isinstance(item, dict)]

    async def get_contact(self, contact_id: str) -> dict[str, Any]:
        """Fetch one Contact by GUID (``GET contacts(<guid>)``).

        Lookup fields come back as ``_<name>_value`` GUID strings.
        """
        return await self.get(f"contacts({contact_id})")

    async def get_contact_id_scheme_ref(
        self,
        *,
        policy_no: str | None = None,
        platform_investor_no: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a Contact ID and Scheme Ref (``POST cam_getcontactidschemeref``).

        Supply a Policy number (e.g. "CT000000743") or a Platform Investor
        number (e.g. "MGI00026030") — one or the other. Returns ``contactId``
        and ``schemeRef`` (e.g. "SSAS/00297") alongside the response envelope.
        """
        body: dict[str, Any] = {}
        if policy_no is not None:
            body["policyNo"] = policy_no
        if platform_investor_no is not None:
            body["platformInvestorNo"] = platform_investor_no
        if not body:
            raise ValueError("supply policy_no or platform_investor_no")
        return await self.call_action("cam_getcontactidschemeref", body)

    async def update_contact(self, contact_id: str, fields: dict[str, Any]) -> str | None:
        """Update a Contact (``PATCH contacts(<guid>)``); returns the OData-EntityId.

        Accepts any Create Contact body fields (same enums apply). Lookup field
        names must carry the ``@odata.bind`` suffix and take entity paths, not
        bare GUIDs — e.g. ``"cam_FinancialAdviserContact@odata.bind":
        "/contacts(<guid>)"`` and ``"cam_financialadviser_Account@odata.bind":
        "/accounts(<guid>)"``.
        """
        return await self.patch_entity(f"contacts({contact_id})", fields)

    async def submit_atr(
        self,
        client_id: int,
        risk_profile: int,
        date_generated: str,
        atr_data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create an Attitude to Risk record for a Contact (``POST cam_submitatr``).

        client_id is the int Client Reference Number (not a GUID); risk_profile
        is 1–10; date_generated must parse as a DateTime (e.g. "2020-04-05").
        The API wants ``atrData`` as a JSON-formatted *string*, so atr_data
        (elements: question_id int, question str, response str) is serialised
        with ``json.dumps`` here. Returns ``contactId`` plus the envelope.
        """
        return await self.call_action(
            "cam_submitatr",
            {
                "clientId": client_id,
                "riskProfile": risk_profile,
                "dateGenerated": date_generated,
                "atrData": json.dumps(atr_data),
            },
        )
