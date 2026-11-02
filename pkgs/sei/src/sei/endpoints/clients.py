"""Client (party) reads and profile maintenance.

The read (`retrievePartyDetails`) is the one to reach for when testing: it answers with the
whole party — names, tax info, compliance block, addresses/phones/emails, **owned accounts**
and the party's DSR grants — from either an external party id or SEI's own entity id. It is
how you confirm an enrolment landed as sent without waiting for the next nightly drop.

The writes are narrow on purpose. `updateClientBasicProfile` is the only door to the client's
own attributes and it takes them as a flat `clientBasicsUpdateData` block; contact details
(address / phone / email) are a separate service because SEI models each as a *profile* with
its own id and primary flag, so "change the email" is really "add a profile and make it
primary" or "update profile 58969698".
"""

from __future__ import annotations

from typing import Any

from sei._base import BaseClient
from sei.endpoints._shapes import attributes as _attributes
from sei.endpoints._shapes import rows as _rows


class ClientsApi(BaseClient):
    """Read a party; maintain its profile and contact details."""

    # --- reads ------------------------------------------------------------------

    async def party_details(
        self,
        firm_id: int | str,
        *,
        external_party_id: str | None = None,
        entity_id: int | str | None = None,
    ) -> dict[str, Any]:
        """The whole party (``POST /getpartydetailsservice/retrievePartyDetails``).

        Give the external party id (ours — `crm.contacts.uid` is the value to use) or SEI's
        `entityId`. `ownedAccounts[]` in the answer is what makes this the cheapest way to
        check "did the account open, and is it attached to the right person".
        """
        attributes: dict[str, Any] = {"firmId": str(firm_id)}
        if external_party_id:
            attributes["externalPartyId"] = external_party_id
        if entity_id is not None:
            attributes["entityId"] = str(entity_id)
        body = await self.request(
            "POST",
            "/getpartydetailsservice/retrievePartyDetails",
            json={"data": {"type": "EntityDetailsRequest", "attributes": attributes}},
        )
        return _attributes(body)

    async def entity_profile_details(
        self, firm_id: int | str, entity_id: int | str
    ) -> dict[str, Any]:
        """The party's profile records (``POST /getpartydetailsservice/getEntityProfileDetails``)
        — the address/phone/email profile ids `update_client_contact` needs to target one."""
        body = await self.request(
            "POST",
            "/getpartydetailsservice/getEntityProfileDetails",
            json={"data": {"attributes": {"firmId": str(firm_id), "entityId": str(entity_id)}}},
        )
        return _attributes(body)

    async def client_contacts(
        self,
        firm_id: int | str,
        *,
        external_party_id: str | None = None,
        party_id: int | str | None = None,
    ) -> list[dict[str, Any]]:
        """Contact details on file (``GET /retrieveclientcontactsservice/clientcontactdetails``)."""
        params: dict[str, Any] = {"firmId": str(firm_id)}
        if external_party_id:
            params["externalPartyId"] = external_party_id
        if party_id is not None:
            params["partyId"] = str(party_id)
        return _rows(
            await self.request(
                "GET", "/retrieveclientcontactsservice/clientcontactdetails", params=params
            )
        )

    # --- writes -----------------------------------------------------------------

    async def update_client_profile(
        self,
        firm_id: int | str,
        update: dict[str, Any],
        *,
        external_party_id: str | None = None,
        entity_id: int | str | None = None,
        platform_user: str = "",
    ) -> dict[str, Any]:
        """Update the client's own attributes
        (``POST /updateclientbasicsprofileservice/updateClientBasicProfile``).

        ``update`` is the `clientBasicsUpdateData` block — `title`, `gender`, `dateOfBirth`,
        `maritalStatus`, `firstCountryPassport`, `politicalExposureIndicator`,
        `initialSourceOfFunds`, `vulnerableClientFlag`, `clientRiskRating`, `baseCurrency`…

        **A 200 here can still be a partial success.** SEI answers `updateStatus: SUCCESS`
        with a `warning[]` listing fields it declined as not applicable to this client type
        (sending `organizationType` for an individual is the documented example), so the
        warnings have to be read rather than the status trusted.

        Note the date format is SEI's display one here (``06-DEC-1978``), not the ISO form the
        cash services take — the same service accepting two conventions is a real trap.
        """
        identifiers: dict[str, Any] = {}
        if external_party_id:
            identifiers["externalPartyId"] = external_party_id
        if entity_id is not None:
            identifiers["endClientId"] = str(entity_id)
        body = await self.request(
            "POST",
            "/updateclientbasicsprofileservice/updateClientBasicProfile",
            json={
                "data": {
                    "type": "clientBasicsUpdateRequest",
                    "attributes": {
                        "firmId": str(firm_id),
                        "platformUser": platform_user,
                        "endClientIdentifiers": identifiers,
                        "clientBasicsUpdateData": update,
                    },
                }
            },
        )
        return _attributes(body)

    async def add_client_contact(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Add an address / phone / email profile (``POST /addclientcontactservice/clientContact``).

        The body is flat and NOT JSON:API-wrapped on this service — `firmId`,
        `externalPartyId`, `contactDetailType` (``Email``/``Phone``/``Address``),
        `profilePrimaryIndicator`, plus one `updateClientContact{Email,Phone,Address}` block.
        """
        return _attributes(
            await self.request("POST", "/addclientcontactservice/clientContact", json=payload)
        )

    async def update_client_contact(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Update one contact profile (``PUT /updateclientcontactservice/clientContact``).

        Same flat shape as :meth:`add_client_contact`. Returns `updatedProfileId` — which is
        a NEW id when SEI chose to supersede rather than edit, so record what comes back
        rather than assuming the id you sent is still current.
        """
        return _attributes(
            await self.request("PUT", "/updateclientcontactservice/clientContact", json=payload)
        )

    async def delete_client_contact(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Remove a contact profile (``DELETE /deleteclientcontactservice/clientContact``)."""
        return _attributes(
            await self.request("DELETE", "/deleteclientcontactservice/clientContact", json=payload)
        )

    async def set_electronic_delivery(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Switch statement/tax delivery to electronic
        (``POST /deliverymethodstoelectronicservice/deliveryMethodsToElectronic``).

        Worth setting on every account we open: the default is Print, and a printed statement
        for a client who only ever uses the portal is a cost and a data-protection surface
        with no reader.
        """
        return _attributes(
            await self.request(
                "POST",
                "/deliverymethodstoelectronicservice/deliveryMethodsToElectronic",
                json=payload,
            )
        )
