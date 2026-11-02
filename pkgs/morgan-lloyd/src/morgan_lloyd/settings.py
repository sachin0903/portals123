"""Runtime settings. Instantiate lazily (inside functions) so importing the
package succeeds with no environment configured."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class MorganLloydSettings(BaseSettings):
    """Morgan Lloyd CRM API connection + auth. All vars are ``MORGAN_LLOYD_*``.

    The API is a Microsoft Dynamics 365 instance: server-to-server auth is an
    Azure AD (v1) client-credentials grant against ``login.microsoftonline.com``
    with the Dynamics organisation URL as the ``resource``. Morgan Lloyd supply
    the tenant_id / client_id / client_secret (crmsupport@clifton-asset.co.uk).
    """

    # BOTH files, later wins -- the convention every consumer of this class actually uses.
    #
    # It was `.env` alone, which is right for `services/morgan-lloyd` (no .env.local) and wrong
    # for the API, whose secrets live in `.env.local` by convention. The result was that
    # `MORGAN_LLOYD_CLIENT_ID` / `_SECRET` / `_TENANT_ID` placed exactly where `.env.example`
    # says to put them were read by NOTHING in `apps/api`: `benefits.py` had noticed and passed
    # `_env_file=(".env", ".env.local")` at one call site, while `sync.py`, `platform_actions`
    # and the client's own default constructor kept getting an unconfigured object. Fixing it
    # here rather than at each call site is the point -- the next call site would have inherited
    # the bug, and the failure is silent until a live call 401s.
    model_config = SettingsConfigDict(
        env_prefix="MORGAN_LLOYD_", env_file=(".env", ".env.local"), extra="ignore"
    )

    # Dynamics organisation URL — the OAuth ``resource`` and the API host.
    # Dev environment: https://cliftonassetdev.crm11.dynamics.com
    resource_url: str = "https://cliftonassetdev.crm11.dynamics.com"
    api_path: str = "/api/data/v9.1"
    login_url: str = "https://login.microsoftonline.com"
    tenant_id: str = ""  # GUID of the Azure directory Morgan Lloyd created
    client_id: str = ""  # Application (client) ID of the registered Azure app
    client_secret: str = ""
    concurrency: int = 8
    timeout: float = 120.0

    # The adviser FIRM this connection's clients belong to, as a Dynamics `accounts` GUID.
    # `cam_createcontact` takes it as `cam_financialadviser_Account`, and a contact created
    # without it cannot have a scheme created against it — `cam_createscheme` answers
    # `403 0x8004f502 ... does not have create permissions to a ML Team secured field`.
    # Verified: a pre-existing contact carrying `_cam_financialadviser_value` reaches the create
    # path and is refused only by a business rule, while one we created without it 403s.
    #
    # One per connection rather than per client, because the model is one Dynamics/Azure tenant
    # per firm — the same assumption `tenant_id` already encodes. A firm running several adviser
    # entities inside one Dynamics org would need this on `platform.tenant_platforms.config`
    # instead; nothing here forecloses that.
    adviser_firm_id: str = ""

    # `cam_createscheme` requires a schemeType and a sippType. ML's SIPP covers scheme types 16
    # and 17, and the sipp_type values are documented WITHOUT labels — both are firm-specific, so
    # they are configuration rather than constants guessed in code. Unset means scheme creation
    # refuses with a message naming them instead of sending a plausible-looking wrong value into a
    # real back-office system.
    scheme_type: int | None = None
    sipp_type: int | None = None

    # `cam_createwrapper` and `cam_createpolicyvaluation` take a `provider` the vendor describes
    # as "The Firm ID we provided you" -- THE SAME PHRASE, and the same value, as
    # `cam_financialadviser_Account` above.
    #
    # Verified against the live org rather than inferred from the wording: every synced contact
    # carries `_cam_financialadviser_value = 6ac39921-d8b7-f011-bbd3-7ced8d6b85ff`, which is
    # exactly the `provider` GUID the API Access Document lists for Create Investment. So it is
    # ONE fact under two field names, and `adviser_firm_id` is the canonical home -- which means
    # the firm id was never actually unknown, it was written down under the other name while
    # `MORGAN_LLOYD_ADVISER_FIRM_ID` sat blank, leaving every synced contact unattached and
    # `raw.adviser_contacts` empty.
    #
    # Kept as its own setting rather than merged: the vendor could issue a firm a different
    # `provider` account from its adviser firm, and a deployment where they differ must be able
    # to say so. Blank means "the same as the adviser firm", not "unset".
    investment_provider_id: str = ""

    # The adviser CONTACT -- a person in the org, distinct from the firm above and from the Azure
    # directory. Required TOGETHER WITH the firm: attaching a contact to `adviser_firm_id` without
    # it is refused outright (`400 0x80040224 Financial Adviser contact must be populated for the
    # adviser firm`), which is the `No*` conditional the vendor documents in prose and which only
    # bites once the firm IS configured. Discoverable rather than issued --
    # `get_financial_adviser_contacts(firm_id)` returns it -- so blank is usable when the firm has
    # exactly one adviser; see `app/integrations/morgan_lloyd/contacts.py`.
    adviser_contact_id: str = ""

    @property
    def investment_provider(self) -> str:
        """The `provider` GUID, falling back to the adviser firm they are issued as."""
        return self.investment_provider_id or self.adviser_firm_id

    @property
    def api_base_url(self) -> str:
        return f"{self.resource_url.rstrip('/')}{self.api_path}"

    @property
    def token_url(self) -> str:
        return f"{self.login_url.rstrip('/')}/{self.tenant_id}/oauth2/token"
