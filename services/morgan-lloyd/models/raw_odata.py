"""Whole-collection extractors over Dataverse's OData entity sets.

The CRM's custom `cam_*` actions are how you WRITE to Morgan Lloyd, and a handful of them
read — but they read one scheme or one contact at a time, which is why the older
`raw_scoped.py` models need `ML_SYNC_SCHEME_REFS` and land nothing without it. Dataverse
also exposes every table as a plain OData collection, and those enumerate themselves.

That distinction matters more than it sounds: `raw.schemes`, `raw.contribution_applications` and
`raw.received_contributions` used to be scope-bound and landed EMPTY TABLES on every run, because
`ML_SYNC_SCHEME_REFS` was never set. The premise recorded in `raw_scoped.py` — that the CRM cannot
enumerate schemes for us — is not true: `cam_schemerefs` lists them and `cam_schemes` can simply be
read whole. Those three now live here and the scope-bound versions are retired.

**Coverage.** The org publishes 157 `cam_*` entity sets. Of the 163 probed (all `cam_*` plus the
standard tables we care about) 38 return rows, 39 are empty and 85 return 403. The 403 set is
**out of scope**: none of those tables appears in Morgan Lloyd's published API documentation
(dev.api.morgan-lloyd.co.uk), so they are not part of the contract and their absence is not a
permissions gap to chase. What is listed below is the domain-relevant subset of what we CAN read
— audit, email, task, annotation and checklist tables are readable and deliberately skipped,
having no consumer and carrying personal data we have no reason to copy.

Every model is `full_merge`: Dataverse offers no change feed on these tables, so each pull is
the whole collection and interlace applies it as a set-difference diff.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
from interlace import model
from morgan_lloyd import MorganLloydClient
from morgan_lloyd.settings import MorganLloydSettings

from ml_sync.records import records_to_table

# Nightly, staggered after the existing whole-book pulls at 02:00.
_NIGHTLY = "30 2 * * *"
# The reference-ish collections change rarely and are large; weekly is enough.
_WEEKLY = "0 5 * * 1"


@dataclass(frozen=True)
class CollectionSpec:
    """One OData entity set we land whole."""

    name: str  # model name, e.g. "raw.schemes"
    entity_set: str  # Dataverse collection, e.g. "cam_schemes"
    key: str  # the table's primary key column
    cron: str
    description: str


COLLECTIONS: tuple[CollectionSpec, ...] = (
    # --- schemes and their references -------------------------------------------------
    CollectionSpec(
        "raw.schemes",
        "cam_schemes",
        "cam_schemeid",
        _NIGHTLY,
        "Every scheme (GET cam_schemes). Replaces the scope-bound raw.schemes, which "
        "landed empty on every run because ML_SYNC_SCHEME_REFS was never set",
    ),
    CollectionSpec(
        "raw.scheme_refs",
        "cam_schemerefs",
        "cam_schemerefid",
        _NIGHTLY,
        "Scheme references (GET cam_schemerefs) — the enumeration "
        "raw_scoped.py needed ML_SYNC_SCHEME_REFS for",
    ),
    CollectionSpec(
        "raw.scheme_assets",
        "cam_schemeassets",
        "cam_schemeassetid",
        _NIGHTLY,
        "Assets held by a scheme (GET cam_schemeassets)",
    ),
    CollectionSpec(
        "raw.scheme_deals",
        "cam_schemedeals",
        "cam_schemedealid",
        _NIGHTLY,
        "Scheme deals (GET cam_schemedeals)",
    ),
    # --- contributions ------------------------------------------------------------------
    CollectionSpec(
        "raw.pension_contributions",
        "cam_pensioncontributions",
        "cam_pensioncontributionid",
        _NIGHTLY,
        "Received pension contributions (GET cam_pensioncontributions) — 1,552 rows the "
        "scope-bound raw.received_contributions never returned before it was retired",
    ),
    CollectionSpec(
        "raw.contributions",
        "cam_contributions",
        "cam_contributionid",
        _NIGHTLY,
        "Contribution applications (GET cam_contributions). Replaces the scope-bound "
        "raw.contribution_applications",
    ),
    # --- benefits and policies ----------------------------------------------------------
    CollectionSpec(
        "raw.benefit_applications",
        "cam_benefitsapplications",
        "cam_benefitsapplicationid",
        _NIGHTLY,
        "Benefit applications / drawdown (GET cam_benefitsapplications)",
    ),
    CollectionSpec(
        "raw.policies",
        "cam_policies",
        "cam_policyid",
        _NIGHTLY,
        "Policies (GET cam_policies)",
    ),
    CollectionSpec(
        "raw.policy_events",
        "cam_policyevents",
        "cam_policyeventid",
        _NIGHTLY,
        "Policy events (GET cam_policyevents)",
    ),
    # --- money and reference ------------------------------------------------------------
    CollectionSpec(
        "raw.fees",
        "cam_fees",
        "cam_feeid",
        _NIGHTLY,
        "Fees (GET cam_fees)",
    ),
    CollectionSpec(
        "raw.platforms",
        "cam_platforms",
        "cam_platformid",
        _WEEKLY,
        "Platforms (GET cam_platforms)",
    ),
    # --- client-facing ------------------------------------------------------------------
    CollectionSpec(
        "raw.enquiries",
        "cam_enquiries",
        "cam_enquiryid",
        _NIGHTLY,
        "Enquiries (GET cam_enquiries)",
    ),
    CollectionSpec(
        "raw.messages",
        "cam_messages",
        "cam_messageid",
        _NIGHTLY,
        "Secure messages (GET cam_messages)",
    ),
    CollectionSpec(
        "raw.vulnerability_flags",
        "cam_vulnerabilityandrepresentatives",
        "cam_vulnerabilityandrepresentativeid",
        _NIGHTLY,
        "Vulnerability and representative records (GET cam_vulnerabilityandrepresentatives)",
    ),
    # --- N:N link tables ------------------------------------------------------------------
    # Dataverse exposes many-to-many relationships as their own collections. They carry only
    # the two foreign keys, and without them a personal policy, liability or trust cannot be
    # attributed to the contact it belongs to.
    CollectionSpec(
        "raw.link_policy_contact",
        "cam_cam_personalpolicy_contactset",
        "cam_cam_personalpolicy_contactid",
        _NIGHTLY,
        "Personal policy <-> contact links",
    ),
    CollectionSpec(
        "raw.link_liability_contact",
        "cam_cam_liability_contactset",
        "cam_cam_liability_contactid",
        _NIGHTLY,
        "Liability <-> contact links",
    ),
    CollectionSpec(
        "raw.link_trust_contact",
        "cam_cam_trust_contactset",
        "cam_cam_trust_contactid",
        _NIGHTLY,
        "Trust <-> contact links",
    ),
)


def _extractor(spec: CollectionSpec) -> Callable[[], Coroutine[Any, Any, pa.Table]]:
    async def pull() -> pa.Table:
        async with MorganLloydClient() as api:
            records = await api.get_collection(spec.entity_set)
        # One Dynamics/Azure tenant per firm, stamped on every row — the discriminator the
        # core->tenant fan-out filters by.
        ml_tenant = MorganLloydSettings().tenant_id
        records = [{**record, "mlTenantId": ml_tenant} for record in records]
        return records_to_table(records, spine={spec.key: pa.string(), "ml_tenant_id": pa.string()})

    pull.__name__ = spec.name
    return pull


for _spec in COLLECTIONS:
    model(
        name=_spec.name,
        strategy="full_merge",
        key=(_spec.key,),
        schedule={"cron": _spec.cron},
        tags=("morgan_lloyd", "raw", "odata"),
        description=_spec.description,
    )(_extractor(_spec))
