"""Whole-book list extractors, landing in the ``raw`` schema. The CRM offers
no change signal (no webhooks, no updated-since filters), so each pull is
necessarily the full list — but ``full_merge`` applies it as a SQL
set-difference diff (insert new/changed, delete changed/vanished keys), so an
unchanged entity writes nothing.

Keys follow Dynamics' primary-key convention (``<entity logical name>id``);
where the docs don't state the key it is noted on the spec."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
from interlace import model
from morgan_lloyd import MorganLloydClient
from morgan_lloyd.settings import MorganLloydSettings

from ml_sync.records import records_to_table


@dataclass(frozen=True)
class ListSpec:
    name: str  # schema-qualified model name, e.g. "raw.contacts"
    method: str  # MorganLloydClient coroutine returning list[dict]
    key: str
    cron: str
    description: str
    # Columns the conformed model reads that a batch can omit entirely. An omitted
    # field is not a column, and the model then fails to bind.
    extra: tuple[str, ...] = ()


LIST_ENTITIES: tuple[ListSpec, ...] = (
    ListSpec(
        "raw.contacts",
        "get_all_contacts",
        "contactid",
        "0 2 * * *",
        "All CRM contacts (GET /contacts)",
        (
            "mobilephone",
            "telephone1",
            "modifiedon",
            "firstname",
            "middlename",
            "lastname",
            "fullname",
            "birthdate",
            "emailaddress1",
            "address1_line1",
            "address1_line2",
            "address1_city",
            "address1_stateorprovince",
            "address1_postalcode",
            "address1_country",
            "statecode",
            "createdon",
        ),
    ),
    ListSpec(
        # cam_personalpolicies is the transfers entity set; key per Dynamics
        # convention (the docs leave the collection's record shape undocumented)
        "raw.transfers",
        "get_all_transfers",
        "cam_personalpolicyid",
        "0 2 * * *",
        "All transfers / personal policies (GET cam_personalpolicies)",
        (
            "modifiedon",
            "cam_name",
            "cam_status",
            "cam_transferstatus",
            "cam_policyholderid_value",
            "cam_providerid_value",
            "cam_financialtransactionid_value",
            "statecode",
            "createdon",
        ),
    ),
    ListSpec(
        "raw.beneficiaries",
        "get_all_beneficiaries",
        "cam_nominationofbeneficiaryid",
        "0 2 * * *",
        "All nomination-of-beneficiary records (GET cam_nominationofbeneficiaries)",
        (
            "modifiedon",
            "cam_name",
            "cam_nameofbeneficiary",
            "cam_firstname",
            "cam_surname",
            "cam_dateofbirth",
            "statecode",
            "createdon",
        ),
    ),
    ListSpec(
        "raw.employer_accounts",
        "get_employer_accounts",
        "accountid",
        "0 4 * * 1",
        "Employer accounts (POST cam_getemployeraccounts)",
    ),
    ListSpec(
        "raw.providers",
        "get_all_providers",
        "cam_providerid",
        "0 4 * * 1",
        "Provider records (POST cam_getallproviders)",
    ),
)


def _extractor(spec: ListSpec) -> Callable[[], Coroutine[Any, Any, pa.Table]]:
    # Closed over as a tuple of strings. Interlace fingerprints the function plus
    # its closure, and a dataclass is hashed by type only — editing ``spec.extra``
    # would not rebuild the table, so an empty listing would keep the narrow
    # schema and the platform model would fail to bind ``modifiedon``.
    columns = (spec.key, "ml_tenant_id", *spec.extra)

    async def pull() -> pa.Table:
        async with MorganLloydClient() as api:
            puller: Callable[[], Coroutine[Any, Any, list[dict[str, Any]]]] = getattr(
                api, spec.method
            )
            records = await puller()
        # Stamp the connection's Microsoft tenant (one Dynamics/Azure tenant per firm):
        # the discriminator the core->tenant fan-out filters by. A multi-firm deployment
        # syncs each connection in turn; every record carries where it came from.
        ml_tenant = MorganLloydSettings().tenant_id
        records = [{**record, "mlTenantId": ml_tenant} for record in records]
        spine = {name: pa.string() for name in columns}
        return records_to_table(records, spine=spine)

    pull.__name__ = spec.name
    return pull


for _spec in LIST_ENTITIES:
    model(
        name=_spec.name,
        strategy="full_merge",
        key=(_spec.key,),
        schedule={"cron": _spec.cron},
        tags=("morgan_lloyd", "raw"),
        description=_spec.description,
    )(_extractor(_spec))
