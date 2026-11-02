"""Financial adviser, scheme and beneficiary endpoints."""

from __future__ import annotations

import json
from typing import Any

from morgan_lloyd._base import BaseClient


class SchemesApi(BaseClient):
    async def get_financial_adviser_contacts(self, firm_id: str) -> list[dict[str, Any]]:
        """Name + ID of every contact under a Financial Adviser Firm
        (``GET contacts``). Plain OData read filtered on
        ``_parentcustomerid_value eq <firm GUID>`` — the GUID is embedded
        unquoted in the ``$filter``. Records carry ``firstname``, ``lastname``
        and ``contactid``."""
        return await self.get_collection(
            "contacts",
            params={
                "$select": "firstname,lastname,contactid",
                "$filter": f"_parentcustomerid_value eq {firm_id}",
            },
        )

    async def create_financial_adviser_contact(self, contact: dict[str, Any]) -> dict[str, Any]:
        """Create a contact under a Financial Adviser Firm
        (``POST cam_CreateFinancialAdviserContact`` — mixed-case, unlike the
        other all-lowercase actions; Dynamics action names are case sensitive).
        Required: ``accountId`` (firm GUID), ``title`` (``Title`` option set),
        ``firstName``, ``lastName``; optional ``emailAddress``. Returns
        ``financialAdviserContactId`` — the new contact's GUID."""
        return await self.call_action("cam_CreateFinancialAdviserContact", contact)

    async def create_scheme(self, scheme: dict[str, Any]) -> dict[str, Any]:
        """Create a scheme for a contact (``POST cam_createscheme``). Required:
        ``contactId`` (GUID), ``schemeType`` (``SchemeType``; ML SIPP covers
        both 16 and 17) and ``sippType`` (``SippType``; values are documented
        without labels — firm-specific, confirm with Morgan Lloyd). Percentage
        fields (``ongoingAdviserFeePercentage``, ``platformFeePercentage``,
        ``investmentCostsPercentage``) must be 0-100. Returns
        ``schemeReference`` (e.g. ``"MLSIPP/00001"``)."""
        return await self.call_action("cam_createscheme", scheme)

    async def update_scheme(self, scheme_ref: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update a scheme's fee percentages by reference
        (``POST cam_updatescheme``). The percentage fields
        (``ongoingAdviserFeePercentage``, ``platformFeePercentage``,
        ``investmentCostsPercentage``) are silently ignored when 0 or null,
        so must be > 0 and <= 100 to take effect. Responds with a bare
        envelope (``responseKey``/``message``, no ``response`` wrapper)."""
        return await self.call_action("cam_updatescheme", {"schemeRef": scheme_ref, **updates})

    async def bulk_update_schemes(self, schemes: list[dict[str, Any]]) -> dict[str, Any]:
        """Update fee percentages on many schemes at once
        (``POST cam_bulkupdateschemes``). Each update needs ``schemeRef``;
        percentage fields are ignored when 0/null (as in ``update_scheme``).
        The API takes the array **double-encoded**: this method ``json.dumps``
        the list into the ``schemeUpdates`` string field. The response
        ``message`` is itself a JSON string of
        ``successfulUpdates``/``failedUpdates``."""
        return await self.call_action(
            "cam_bulkupdateschemes", {"schemeUpdates": json.dumps(schemes)}
        )

    async def get_scheme_by_ref(self, scheme_ref: str) -> dict[str, Any]:
        """Scheme valuation summary by reference (``POST cam_getscheme`` — a
        read implemented as a custom action). Returns ``schemeId``, fund
        amounts, bank balance (can be negative), fee percentages and
        ``fundsLastCalculatedDate``. Gotcha: the response field is
        ``platformFeesPercentage`` (plural "Fees") whereas create/update
        requests use ``platformFeePercentage``."""
        return await self.call_action("cam_getscheme", {"schemeRef": scheme_ref})

    async def get_scheme_review_date(self, scheme_ref: str) -> list[dict[str, Any]]:
        """A scheme's review date by reference (``GET cam_schemes``). Plain
        OData read with the reference single-quoted in the ``$filter``
        (``cam_schemeref eq 'MLSIPP/12345'``); ``$select`` keeps the response
        to ``cam_schemeid`` + ``cam_reviewdate`` (omitting it would return
        every field)."""
        return await self.get_collection(
            "cam_schemes",
            params={
                "$filter": f"cam_schemeref eq '{scheme_ref}'",
                "$select": "cam_schemeid,cam_reviewdate",
            },
        )

    async def create_beneficiary(self, beneficiary: dict[str, Any]) -> dict[str, Any]:
        """Nominate a beneficiary on a scheme (``POST cam_createbeneficiaries``).
        Required: ``schemeReference`` (camelCase — the rest are raw Dynamics
        ``cam_*`` attribute names, case sensitive), ``cam_nameofbeneficiary``,
        ``cam_percentageoffund`` (documented as string, numeric in examples),
        ``cam_nominationdate`` (``"YYYY-MM-DD"``), ``cam_relationship``,
        ``cam_address_line1``, ``cam_city``, ``cam_postcode``. Returns the
        created record under ``beneficiary`` plus ``beneficiaryId``."""
        return await self.call_action("cam_createbeneficiaries", beneficiary)

    async def get_all_beneficiaries(self) -> list[dict[str, Any]]:
        """Every nomination-of-beneficiary record
        (``GET cam_nominationofbeneficiaries``). Plain OData entity-set read,
        no parameters. ``cam_beneficiarystatus`` is a ``BeneficiaryStatus``
        value (0 = Removed is the only documented label; examples show 1 for
        active records)."""
        return await self.get_collection("cam_nominationofbeneficiaries")

    async def get_beneficiaries_by_scheme_ref(self, scheme_ref: str) -> list[dict[str, Any]]:
        """Beneficiaries joined to a scheme by reference
        (``POST cam_schemebeneficiaries`` — a read implemented as POST).
        Records are ``cam_scheme`` entities carrying the joined beneficiary
        fields under OData escaped-alias names (``nom_x002e_...`` — the
        ``_x002e_`` is an encoded dot from the ``nom.`` alias)."""
        body = await self.call_action("cam_schemebeneficiaries", {"schemeReference": scheme_ref})
        return [item for item in body.get("value", []) if isinstance(item, dict)]

    async def get_beneficiary(self, beneficiary_id: str) -> dict[str, Any]:
        """One beneficiary by GUID
        (``GET cam_nominationofbeneficiaries(<guid>)`` — the GUID sits bare
        inside the parentheses, no quotes). Same fields as the
        ``get_all_beneficiaries`` records."""
        return await self.get(f"cam_nominationofbeneficiaries({beneficiary_id})")

    async def update_beneficiary(self, beneficiary_id: str, updates: dict[str, Any]) -> None:
        """Partially update a beneficiary by GUID
        (``PATCH cam_nominationofbeneficiaries(<guid>)`` — standard OData
        PATCH, not a custom action). Any Create Beneficiary field may be sent;
        set ``cam_beneficiarystatus`` to ``BeneficiaryStatus`` Removed (0) to
        remove the beneficiary. The API answers 204 No Content, so this
        returns ``None``."""
        await self.patch_entity(f"cam_nominationofbeneficiaries({beneficiary_id})", updates)
