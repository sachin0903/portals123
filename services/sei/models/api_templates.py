"""``ref.enrollment_templates`` — the one enrolment input the SFTP drop cannot supply.

**Why this model exists, stated precisely, because the temptation is to add API models for
things the drop already has.** The nightly drop carries 47 reports and a Reference dimension
with 183 code sets, and between them they answer almost every id
`POST /accountservice/accounts` needs:

===================================  ============================================
enrolment field                      where it comes from in the drop
===================================  ============================================
``accountTypeId``                    Reference ``ACCT_TYPE``
``accountSubTypeId``                 Reference ``ACCOUNT_SUB_TYPE``
``accountClassificationId``          Reference ``ACCOUNT_CLASSIFICATION`` (per firm)
``accountSubClassificationId``       Reference ``ACCOUNT_SUB_CLASSIFICATION``
``firmInvestmentCapacityId``         Reference ``FIRM_INVESTMENT_CAPACITY``
``clientTypeId``                     Reference ``CLIENT_TYPE``
``titleId`` / ``suffixId``           Reference ``TITLE`` / ``SUFFIX``
``maritalStatusId``                  Reference ``MARITAL_STATUS``
``genderId``                         Reference ``GENDER``
``certifiedFATCAComplianceId``       Reference ``FATCA_COMPLIANCE``
``taxIdTypeId``                      Reference ``TAX_ID_TYPE``
``phoneMethodId``                    Reference ``PHONE_METHOD``
``reinvestmentMethodOfFundsId``      Reference ``MUTUAL_FUND_REINVEST_PREF``
``typeOfTrustId``                    Reference ``TYPE_OF_TRUST``
``taxWrapProviderId``                Reference ``TAX_WRAP_PROVIDER``
``accountQualifierId``               Reference ``ACCOUNT_QUALIFIER``
the DSR ``servicingRelationshipId``  Reference ``DSR`` / ``DSR_TYPE_NAME`` + ``ClientAccountLink``
``portfolioId``                      ``Accounts`` (the report is grained account x portfolio)
===================================  ============================================

**Templates are the exception.** `clientTemplateId` and `accountTemplateId` are REQUIRED, and
there is no Reference `TYPE` for either and no report that carries one. So without this model
an account cannot be opened — which makes two small requests per firm the narrowest possible
unblock for the entire enrolment path, and is the whole justification for reaching for the API
at all.

**The account template is per SHELF PRODUCT, not per wrapper type.** A firm running an
"AIM ISA" and an "AIM IHT" has a template for each while both are ISA/GIA at the wrapper level,
so `platform.product_type` cannot key them: the id belongs on
`platform.product_platforms.account_open_reference`, keyed by product. This
model is the discovery side of that mapping — it lists what exists so an
operator can attach each template to the right shelf product, which is a
decision only they can make.

Disabled by default: a blank ``SEI_API_BASE_URL`` returns an empty typed table, so a deployment
with SFTP credentials and no API credentials runs unchanged rather than failing every night.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

import pyarrow as pa
from interlace import model

from sei_sync.firms import resolve_firm_ids
from sei_sync.pull import fetch_templates
from sei_sync.settings import SeiSettings

logger = logging.getLogger("sei_sync.models")

#: The conformed shape. Ids stay VARCHAR for the reason the whole raw layer does: they are join
#: keys, never arithmetic, and SEI's identifiers carry leading zeros in places.
SCHEMA: dict[str, pa.DataType] = {
    "firm_id": pa.string(),
    "template_id": pa.string(),
    "template_name": pa.string(),
    "template_type": pa.string(),
    "template_type_desc": pa.string(),
    "scope": pa.string(),
    "as_of_date": pa.date32(),
    "fetched_at": pa.timestamp("us", tz="UTC"),
}

#: SEI's key for each conformed column, in the order to try. The two template services answer
#: the same information under different names, and which one you get depends on the service —
#: so the reconciliation is a list rather than a mapping, and an unmatched column lands in
#: neither (it is simply absent, which is visible, rather than silently mistyped).
_ALIASES: dict[str, tuple[str, ...]] = {
    "template_id": ("templateId", "TEMPLATE_ID", "id", "clientTemplateId", "accountTemplateId"),
    "template_name": ("templateName", "TEMPLATE_NAME", "name", "description"),
    "template_type": ("templateType", "TEMPLATE_TYPE", "type"),
    "template_type_desc": ("templateTypeDescription", "TEMPLATE_TYPE_DESC", "typeDescription"),
}


def _pick(row: dict[str, Any], column: str) -> Any:
    for key in _ALIASES.get(column, ()):
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def conform(rows: list[dict[str, Any]], *, as_of: date, fetched_at: datetime) -> pa.Table:
    """SEI's template rows -> the conformed table.

    Values are stringified rather than cast: SEI sends `TEMPLATE_ID` as a number in one service
    and a string in the other, and a column whose type depends on which service answered is
    unjoinable.
    """
    columns: dict[str, list[Any]] = {name: [] for name in SCHEMA}
    for row in rows:
        template_id = _pick(row, "template_id")
        if template_id in (None, ""):
            # A row with no id cannot be used in an enrolment, and keeping it would put a NULL
            # in the column the mapping is keyed on.
            logger.warning("sei ref.enrollment_templates: dropping a row with no template id")
            continue
        columns["firm_id"].append(str(row.get("_firm_id") or ""))
        columns["template_id"].append(str(template_id))
        name = _pick(row, "template_name")
        columns["template_name"].append(None if name is None else str(name))
        kind = _pick(row, "template_type")
        columns["template_type"].append(None if kind is None else str(kind))
        desc = _pick(row, "template_type_desc")
        columns["template_type_desc"].append(None if desc is None else str(desc))
        columns["scope"].append(str(row.get("_scope") or ""))
        columns["as_of_date"].append(as_of)
        columns["fetched_at"].append(fetched_at)
    return pa.table({name: pa.array(values, type=SCHEMA[name]) for name, values in columns.items()})


@model(
    name="ref.enrollment_templates",
    # `full_merge`, not `merge`: this is a full statement of what the firm has TODAY, and a
    # template SEI has retired must stop appearing. Merging would leave a withdrawn template in
    # the picker for ever, and an enrolment against one is refused in a way that names the
    # template rather than the fact it no longer exists. `replace` would drop the table mid-apply.
    strategy="full_merge",
    key=("firm_id", "scope", "template_id"),
    # The firm list is the Reference report. A run still reads the last built `ref.firms`
    # when today's drop has not rebuilt it, so a failed file does not empty the picker.
    depends_on=("ref.firms",),
    schedule={"cron": "30 7 * * *"},
    tags=("sei", "ref", "api"),
    description="SEI enrolment templates per firm (client + account) — API-only reference data",
)
async def ref_enrollment_templates(ref_firms: Any = None) -> pa.Table:
    settings = SeiSettings()
    now = datetime.now(tz=UTC)
    if not settings.api_base_url:
        logger.info("sei ref.enrollment_templates: SEI_API_BASE_URL unset — API sync disabled")
        return conform([], as_of=now.date(), fetched_at=now)

    firm_ids = resolve_firm_ids(ref_firms)
    if not firm_ids:
        # The Reference report is the list. Guessing "every firm the files mention" would
        # call firms this credential does not serve.
        logger.warning(
            "sei ref.enrollment_templates: the Reference report named no firm — "
            "enrolment templates cannot be discovered"
        )
        return conform([], as_of=now.date(), fetched_at=now)

    rows, stats = await fetch_templates(settings, firm_ids)
    if stats.failures:
        # Not raised: partial reference data is worth having, and the operator needs to see
        # WHICH firm is unreadable rather than losing the whole table to it.
        logger.warning(
            "sei ref.enrollment_templates: %d firm/scope read(s) failed, e.g. %s",
            len(stats.failures),
            next(iter(stats.failures.items())),
        )
    return conform(rows, as_of=now.date(), fetched_at=now)


__all__ = ["SCHEMA", "conform", "ref_enrollment_templates"]
