"""Factfind endpoints: reviews, soft facts, assets, liabilities, relationships."""

from __future__ import annotations

import json
from typing import Any

from morgan_lloyd._base import BaseClient

_EMPLOYER_ACCOUNT_FILTER = (
    "statecode eq 0 and cam_accountstatus eq 0 and customertypecode eq 809130001"
)


def _items(body: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """The list of record dicts under ``key`` (empty if absent or not a list)."""
    value = body.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


class FactfindsApi(BaseClient):
    async def get_client_factfind(self, contact_id: str) -> dict[str, Any]:
        """Retrieve the latest Factfind for a Contact (``POST cam_getclientfactfind``).
        Requires ``contactId``. Returns the parsed body: the record sits under
        ``factFind`` (with its Soft Facts under ``softFacts``); linked-entity fields
        use ``_x002e_`` aliases (e.g. ``employer_x002e_name``,
        ``review_x002e_cam_annualreviewid``) and lookups come back as
        ``_<field>_value``. Enum-typed fields map to ``morgan_lloyd.enums``
        ``StateOfHealth``, ``FactfindEmploymentStatus``, ``RiskLevel``, ``CapacityForLoss``,
        ``SuitabilityConfirmation`` and ``Priority`` (cam_priority1..8)."""
        return await self.call_action("cam_getclientfactfind", {"contactId": contact_id})

    async def upsert_review_factfind(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create or update a Review Factfind (``POST cam_createclientfactfind``).
        Creating also creates a Review Meeting record, and an Attitude to Risk record
        when ``cam_agreedcapacityforloss`` is included (``atrId`` in the response).
        Required: ``cam_client`` (Contact guid), ``cam_meetingdate``, ``cam_smoker``
        (true/false). Include ``cam_factfindid`` ONLY when updating an existing
        Factfind. Gotchas: income/expenditure decimals will NOT update when 0/null is
        provided; ``cam_atrcomments``/``cam_capacityforlosscomments`` require
        ``cam_agreedcapacityforloss``; the Will/LPA fields are the STRINGS "1"/"0",
        not booleans; the docs' field table says ``cam_risklevel1`` for the agreed
        ATR level but the example body uses ``cam_risklevel`` — follow the example.
        Enums in ``morgan_lloyd.enums``: ``CapacityForLoss``, ``StateOfHealth``,
        ``FactfindEmploymentStatus``, ``RiskLevel``, ``Priority``, ``SuitabilityConfirmation``."""
        return await self.call_action("cam_createclientfactfind", body)

    async def upsert_soft_facts(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create or update a Soft Facts record (``POST cam_createupdatesoftfacts``).
        Required: ``cam_factfind`` (Factfind guid), ``cam_topic``
        (``morgan_lloyd.enums.SoftFactTopic``), ``cam_description``. Include
        ``cam_softfactsid`` ONLY when updating. ``cam_name`` defaults to
        '"Client Name" + Soft Fact + Date' when omitted."""
        return await self.call_action("cam_createupdatesoftfacts", body)

    async def get_employer_accounts_filtered(
        self,
        select: str = "name",
        filter: str = _EMPLOYER_ACCOUNT_FILTER,  # shadows the builtin; mirrors OData $filter
    ) -> list[dict[str, Any]]:
        """List active employer Accounts via the built-in Web API
        (``GET accounts?$select=...&$filter=...``) — a standard OData query, NOT a
        custom action (the docs' HTTP Request line erroneously repeats
        ``POST cam_getemployeraccounts``). The default filter selects active
        accounts (``statecode eq 0``, ``cam_accountstatus eq 0``) that are employers
        (``customertypecode eq 809130001``). Returns the ``value`` records
        (``accountid``, ``name``), following pagination."""
        return await self.get_collection("accounts", params={"$select": select, "$filter": filter})

    async def get_employer_accounts(self) -> list[dict[str, Any]]:
        """List all Employer Accounts (``POST cam_getemployeraccounts``). No request
        body. Returns the ``employerAccounts`` records (``accountid``, ``name``)."""
        return _items(await self.call_action("cam_getemployeraccounts", {}), "employerAccounts")

    async def upsert_employer_account(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create or update an Employer Account and link it to a Contact
        (``POST cam_createupdateaccount``). Required: ``contactid``. Include
        ``accountid`` to update/link an existing Account; provide ``name`` only when
        renaming an existing Account or creating a new one."""
        return await self.call_action("cam_createupdateaccount", body)

    async def get_client_assets(
        self, contact_id: str, entity_type: str | None = None
    ) -> list[dict[str, Any]]:
        """List a Contact's assets (``POST cam_getclientassets``). Requires
        ``contactId``; ``entityType`` optionally filters to one of
        ``cam_personalpolicy`` (Personal Assets), ``cam_policy`` (Non-Scheme
        Policies) or ``cam_scheme`` (Schemes) — the ``assets`` collection mixes all
        three. Items are expandos with ``@odata.type`` annotations; ``provider`` and
        ``wrapperApplication`` are nested name+guid entities. Contribution
        frequencies map to ``morgan_lloyd.enums.ContributionFrequency``. Returns the
        ``assets`` records."""
        body: dict[str, Any] = {"contactId": contact_id}
        if entity_type is not None:
            body["entityType"] = entity_type
        return _items(await self.call_action("cam_getclientassets", body), "assets")

    async def upsert_personal_asset(
        self, body: dict[str, Any], joint_assets: list[str] | None = None
    ) -> dict[str, Any]:
        """Create or update a Personal Asset (``POST cam_createupdatepersonalasset``).
        Required: ``cam_policyholderid`` (Contact guid), ``cam_status``
        (``morgan_lloyd.enums.AssetStatus``), ``cam_assettype``
        (``morgan_lloyd.enums.AssetType``). Include ``cam_personalpolicyid`` ONLY
        when updating. ``joint_assets`` is the list of joint-holder Contact guids
        (EXCLUDING the primary ``cam_policyholderid``), JSON-encoded into the
        ``jointAssets`` body field as a string containing a JSON array — required
        when creating a Joint Asset. Gotchas: ``cam_nonprcv``,
        ``cam_approxcostofacquisition``, ``cam_protectioninitialsumassured`` and
        ``cam_protectionpremium`` will NOT update when 0/null is provided; the docs'
        field table shows camelCase ``cam_assetType``/``cam_wrapperApplication`` but
        the example body uses lowercase ``cam_assettype``/``cam_wrapperapplication``
        — follow the example (Dynamics attribute names are lowercase);
        ``cam_protectionfrequency`` is documented as a string but the example sends
        the option-set integer. Other enums in ``morgan_lloyd.enums``:
        ``InvestmentType``, ``PensionType``, ``ProtectionType``,
        ``ProtectionPremiumBasis``, ``ProtectionPremiumFrequency``,
        ``UnderwritingTerms``, ``ContributionFrequency``."""
        payload = dict(body)
        if joint_assets is not None:
            payload["jointAssets"] = json.dumps(joint_assets)
        return await self.call_action("cam_createupdatepersonalasset", payload)

    async def get_client_liabilities(self, contact_id: str) -> list[dict[str, Any]]:
        """List a Contact's liabilities (``POST cam_getclientliabilities``). Requires
        ``contactId``. Returns the ``liabilities`` records (``cam_liability``
        entities; ``cam_liabilitytype`` / ``cam_mortgagetype`` map to
        ``morgan_lloyd.enums.LiabilityType`` / ``MortgageType``; the linked Provider
        guid is ``_cam_provider_value``)."""
        return _items(
            await self.call_action("cam_getclientliabilities", {"contactId": contact_id}),
            "liabilities",
        )

    async def upsert_liability(
        self, body: dict[str, Any], joint_liabilities: list[str] | None = None
    ) -> dict[str, Any]:
        """Create or update a Liability (``POST cam_createupdateliability``). Required:
        ``cam_contact`` (Contact guid), ``cam_liabilitytype``
        (``morgan_lloyd.enums.LiabilityType``; ``cam_mortgagetype`` maps to
        ``MortgageType``). Include ``cam_liabilityid`` ONLY when updating.
        ``joint_liabilities`` is the list of joint-holder Contact guids (EXCLUDING
        the primary ``cam_contact``), JSON-encoded into the ``jointLiabilities`` body
        field as a string containing a JSON array — required when creating a Joint
        Liability. Gotcha: ``cam_balance`` and ``cam_interestrate`` will NOT update
        when 0/null is provided (the docs' footnote marker is inconsistent, but they
        are the starred fields)."""
        payload = dict(body)
        if joint_liabilities is not None:
            payload["jointLiabilities"] = json.dumps(joint_liabilities)
        return await self.call_action("cam_createupdateliability", payload)

    async def get_client_relationships(self, contact_id: str) -> list[dict[str, Any]]:
        """List a Contact's relationships (``POST cam_getclientrelationships``).
        Requires ``contactId``. Returns the ``relationships`` records (``connection``
        entities; linked-entity aliases use ``_x002e_``, e.g. ``role_x002e_name``,
        ``contact_x002e_fullname``; ``cam_financiallydependent`` is 1=Yes / 0=No).
        Note: the raw response's top-level ``dependentCount`` is not returned here."""
        return _items(
            await self.call_action("cam_getclientrelationships", {"contactId": contact_id}),
            "relationships",
        )

    async def upsert_relationship(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create or update a Relationship (``POST cam_createupdaterelationship``).
        Required: ``record1id``/``record2id`` (From/To Contact guids) and
        ``record1roleid``/``record2roleid`` — fixed Relationship Type guids from the
        docs' lookup table (Child/Grandchild/Grandparent/Parent/Sibling/Spouse).
        Include ``connectionid`` ONLY when updating. ``cam_financiallydependent`` is
        1=Yes / 0=No; ``cam_dependentuntil`` is a DateTime string."""
        return await self.call_action("cam_createupdaterelationship", body)

    async def create_note(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create a Note (annotation) against a Review record (``POST
        cam_createnote``). Required: ``objectid`` (the Review guid, e.g. from Get
        Client Factfind's ``review_x002e_cam_annualreviewid``), ``entityType`` (only
        documented value: ``cam_annualreview``), ``subject``, ``notetext``."""
        return await self.call_action("cam_createnote", body)

    async def create_task(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create a Task against a Review record (``POST cam_createtask``). Required:
        ``regardingobjectid`` (the Review guid), ``entityType`` (only documented
        value: ``cam_annualreview``), ``ownerid`` and ``ownerEntityType``
        (``systemuser`` or ``team`` — despite the docs' copy-pasted description,
        ``ownerid`` is the Task OWNER's guid, not the related record), ``subject``.
        Optional: ``description``, ``scheduledend`` (due date string)."""
        return await self.call_action("cam_createtask", body)

    async def get_all_providers(self) -> list[dict[str, Any]]:
        """List all Providers (``POST cam_getallproviders``). No request body. Unlike
        the other list endpoints, the records array sits directly under ``response``
        (no responseKey expando) — returns those ``cam_provider`` records
        (``cam_providerid``, ``cam_name``); the guids are used as
        ``_cam_provider_value`` on Liabilities and ``provider.cam_provider`` on
        Assets."""
        return _items(await self.call_action("cam_getallproviders", {}), "response")
