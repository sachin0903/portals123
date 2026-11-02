"""Transfer and pension-contribution endpoints."""

from __future__ import annotations

import json
from typing import Any

from morgan_lloyd._base import BaseClient


class TransfersApi(BaseClient):
    """Transfers (``cam_personalpolicy``) and pension contributions
    (``cam_contribution`` / ``cam_pensioncontribution``)."""

    # --- transfers --------------------------------------------------------------

    async def create_transfer(self, transfer: dict[str, Any]) -> dict[str, Any]:
        """Create a Transfer (``POST cam_createtransfer``). Required:
        ``schemeReference``, ``cam_policyno``, ``cam_providername``,
        ``cam_assettype`` (Pension Plan=1), ``cam_status`` (Transferring=4),
        ``cam_uncrystalllisedtransfervalue`` (verbatim triple-l spelling —
        "uncrystalllised" — do not correct it), ``cam_crystallisedtransfervalue``,
        ``cam_nonprtv`` (total transfer value), ``cam_typeoftransfer`` and
        ``cam_transfertype``. ``cam_pensiontype`` (``PensionType`` in
        ``morgan_lloyd.enums``) is required only when the policy is a pension
        product. Optional ``transferNotes`` creates a Note against the Transfer.
        Returns the response envelope plus ``transfer`` (field echo) and
        ``transferId`` (``cam_personalpolicyid`` GUID)."""
        return await self.call_action("cam_createtransfer", transfer)

    async def get_transfer(self, client_reference: str, policy_no: str) -> dict[str, Any]:
        """Read one Transfer's progress (``POST cam_getpensiontransfer`` — a
        POST despite being a read). Looks up by client contact GUID
        (``clientReference``) + transfer reference (``policyNo``); both are
        required. Returns a flat object (no wrapper array): ``daterequested``,
        ``origotransfer``, ``transferstatus`` (``TransferStatus`` in
        ``morgan_lloyd.enums`` — the 0-5 progress set, NOT the record's
        ``cam_status``), ``expectedtransfer``, ``lastnote``/``lastnotedate``,
        ``actualtv``/``actualcrystallisedtv``/``actualuncrystallisedtv`` and
        ``duedatetaskdate``."""
        body = {"clientReference": client_reference, "policyNo": policy_no}
        return await self.call_action("cam_getpensiontransfer", body)

    async def get_transfer_by_policy_no(self, policy_no: str) -> list[dict[str, Any]]:
        """Query Transfers by reference number (``GET cam_personalpolicies``
        with ``$filter=cam_policyno eq '<policy_no>'``). Can return multiple
        records for the same policy number. Each record carries ``cam_name``,
        ``cam_personalpolicyid`` and the actual transfer values under their
        ``cam_`` keys (``cam_actualnonprtv``/``cam_actualcrystallisedtv``/
        ``cam_actualuncrystallisedtv`` — the docs' unprefixed response table is
        wrong); money values arrive as strings."""
        escaped = policy_no.replace("'", "''")
        params = {
            "$select": (
                "cam_name,cam_personalpolicyid,cam_actualnonprtv,"
                "cam_actualcrystallisedtv,cam_actualuncrystallisedtv"
            ),
            "$filter": f"cam_policyno eq '{escaped}'",
        }
        return await self.get_collection("cam_personalpolicies", params)

    async def get_all_transfers(self) -> list[dict[str, Any]]:
        """List every Transfer (``GET cam_personalpolicies``). A bare OData
        collection read — the docs document no request fields or response
        shape beyond the standard ``value`` array of ``cam_personalpolicy``
        records; pagination is followed automatically."""
        return await self.get_collection("cam_personalpolicies")

    async def get_all_transfers_by_scheme(self, scheme_reference: str) -> list[dict[str, Any]]:
        """List a scheme's Transfers (``POST cam_getalltransfersbyscheme`` — a
        custom action, despite the docs' GET example header). Requires
        ``schemeReference``. Returns the ``transfers`` array; unlike
        :meth:`get_transfer` the record fields are ``cam_``-prefixed
        (``cam_transferstatus`` — ``TransferStatus`` in ``morgan_lloyd.enums``,
        ``cam_daterequested``, ``cam_expectedtransferdate``,
        ``cam_actualnonprtv``, ``cam_policyno``, ``cam_personalpolicyid``,
        ...). ``origotransfer`` is documented but may be absent."""
        body = await self.call_action(
            "cam_getalltransfersbyscheme", {"schemeReference": scheme_reference}
        )
        return [item for item in body.get("transfers", []) if isinstance(item, dict)]

    async def update_transfer(self, transfer_id: str, update: dict[str, Any]) -> str | None:
        """Update a Transfer / Personal Asset by GUID
        (``PATCH cam_personalpolicies(<transfer_id>)``). The documented body is
        asset-transfer fields, all required: ``cam_transferdate``,
        ``cam_transferamount`` and ``cam_fundnotes``. Returns the
        ``OData-EntityId`` header from the 204 response."""
        return await self.patch_entity(f"cam_personalpolicies({transfer_id})", update)

    # --- pension contributions ----------------------------------------------------

    async def create_pension_contribution(self, contribution: dict[str, Any]) -> dict[str, Any]:
        """Create a Pension Contribution (``POST cam_createcontribution``).
        Required: ``schemeReference``, ``cam_frequency``
        (``ContributionFrequency`` in ``morgan_lloyd.enums``), ``cam_amount``,
        ``cam_startdate`` and ``cam_status`` (``ContributionStatus``).
        ``cam_contributiontype`` is ``ContributionType`` (the 8091300xx set —
        NOT ``ReceivedContributionType``). Conditionals: ``cam_contributor``
        (contact/account GUID) is required when the type is Personal (``^``);
        ``cam_employerothername`` when it is NOT Personal (``*``). For an
        Investment Contribution set status Received + frequency Single.
        Returns the response envelope plus ``contribution`` (field echo) and
        ``contributionId`` (``cam_contributionid`` GUID)."""
        return await self.call_action("cam_createcontribution", contribution)

    async def get_pension_contribution_applications(
        self, scheme_reference: str
    ) -> list[dict[str, Any]]:
        """List a scheme's Contribution Applications
        (``POST cam_getpensioncontributionapplications``). Requires
        ``schemeReference``. Returns the ``contributionApplications`` array of
        ``cam_contribution`` records: contributor contact details, ``cam_amount``,
        ``cam_paymenttype`` (``PaymentType`` in ``morgan_lloyd.enums`` — plain
        0-3, not 8091300xx), ``cam_contributiontype`` (``ContributionType``),
        ``cam_status`` (``ContributionStatus``), ``cam_frequency``
        (``ContributionFrequency``), plus scheme/application/transaction lookup
        GUIDs and ``cam_contributionid``."""
        body = await self.call_action(
            "cam_getpensioncontributionapplications", {"schemeReference": scheme_reference}
        )
        return [item for item in body.get("contributionApplications", []) if isinstance(item, dict)]

    async def get_received_contributions(self, scheme_reference: str) -> list[dict[str, Any]]:
        """List a scheme's received contributions
        (``POST cam_getreceivedcontributions``). Requires ``schemeReference``.
        Returns the ``pensionContributions`` array of ``cam_pensioncontribution``
        records (a DIFFERENT entity from ``cam_contribution``): here
        ``cam_contributiontype`` uses ``ReceivedContributionType`` in
        ``morgan_lloyd.enums`` (Personal=0, Employer=1, Other=809130000) — NOT
        ``ContributionType``. Records carry ``_cam_contributor_value``,
        ``cam_contributionamount``, ``cam_pensioncontributionid``,
        ``cam_dateofcontribution`` and (sometimes) the aliased
        ``DateofInvestment_x002e_ft_transactiondate``."""
        body = await self.call_action(
            "cam_getreceivedcontributions", {"schemeReference": scheme_reference}
        )
        return [item for item in body.get("pensionContributions", []) if isinstance(item, dict)]

    async def update_pension_contribution(
        self, contribution_id: str, update: dict[str, Any]
    ) -> dict[str, Any]:
        """Fill in a Pension Contribution's empty fields
        (``POST cam_updatecontribution``). GOTCHA: the request is REJECTED if
        any of the updatable fields is already populated — this only fills
        blanks; to stop a contribution use
        :meth:`update_pension_contribution_status` instead. Updatable fields:
        ``cam_contributiontype`` (``ContributionType`` in
        ``morgan_lloyd.enums``), ``cam_prefundedrasamount``,
        ``cam_investorref``, and the contributor details — ``cam_contributor``
        is required when the type is Personal (``^``);
        ``cam_employerothername``, ``cam_addressline1``, ``cam_city``,
        ``cam_county``, ``cam_postcode``, ``cam_phone`` and ``cam_email`` when
        it is NOT Personal (``*``)."""
        body = {"cam_contributionid": contribution_id, **update}
        return await self.call_action("cam_updatecontribution", body)

    async def update_pension_contribution_status(
        self, contribution_id: str, status: int
    ) -> str | None:
        """Set a Pension Contribution's status by GUID
        (``PATCH cam_contributions(<contribution_id>)``). ``status`` is
        ``ContributionStatus`` in ``morgan_lloyd.enums``; the documented use is
        Stopped (809130002) — when a contribution's value changes, create a new
        one and stop the old. Returns the ``OData-EntityId`` header from the
        204 response."""
        path = f"cam_contributions({contribution_id})"
        return await self.patch_entity(path, {"cam_status": status})

    async def update_contribution_application(
        self, contribution_id: str, update: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a Contribution Application
        (``POST cam_updatecontributionapplication``). Only applications whose
        status is Application can be updated: amend ``cam_amount`` or set
        ``cam_status`` to Stopped (809130002 — the only documented value;
        ``ContributionStatus`` in ``morgan_lloyd.enums``). The GUID goes in the
        body as ``cam_contributionid`` (the docs' "URL Parameters" table is a
        stray — required in practice, sent here automatically)."""
        body = {"cam_contributionid": contribution_id, **update}
        return await self.call_action("cam_updatecontributionapplication", body)

    async def upload_pension_contributions(
        self, contributions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Bulk-create Pension Contributions
        (``POST cam_uploadpensioncontributions``). The body's single
        ``pensioncontributions`` field must be an ESCAPED JSON string —
        ``{"Contributions": [...]}`` (capital C) serialised to a string, not a
        nested object — which this method encodes for you. Each item matches
        :meth:`create_pension_contribution` (required ``schemeReference``,
        ``cam_frequency``/``ContributionFrequency``, ``cam_amount``,
        ``cam_startdate``, ``cam_status``/``ContributionStatus``;
        ``cam_contributiontype`` is ``ContributionType``; ``cam_contributor``
        required when type is Personal — no ``*`` rule here). Returns the
        response envelope plus ``results`` (per-scheme ``SchemeReference`` +
        ``Message``); invalid items report errors there while valid items still
        succeed."""
        payload = json.dumps({"Contributions": contributions})
        return await self.call_action(
            "cam_uploadpensioncontributions", {"pensioncontributions": payload}
        )
