"""Paged pulls over the SWP client.

`SeiApi` is one HTTP call. These walk accounts, firms and pages, and they keep
going when one of those fails. They live here so the client stays the client.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sei_sync.api import (
    DEFAULT_EMPLOYEE_SERVICE_ID,
    MAX_PAGE_SIZE,
    AccountRef,
    ProxyUnavailable,
    SeiApi,
    SyncStats,
    windows,
)
from sei_sync.settings import SeiSettings

logger = logging.getLogger(__name__)


async def fetch_transactions(
    settings: SeiSettings,
    accounts: list[AccountRef],
    start: date,
    end: date,
    *,
    fetched_at: datetime | None = None,
) -> tuple[list[Any], SyncStats]:
    """Fetch every account's transactions over ``start``..``end``, windowed and concurrent.

    Returns a list of Arrow tables, one per account, **not** raw dicts. A three-year
    backfill is ~1.4M transactions, and holding those as Python dicts costs several GB;
    converting each account's rows the moment they arrive keeps peak memory to one
    account's worth and lets the rest live in Arrow's columnar form.

    One account's failure is recorded and the rest continue: a backfill is tens of
    thousands of requests and must not be lost to one bad account. A failed account simply
    has no rows this run, and the next run's overlap re-reads its range.
    """
    from sei_sync.api_rows import flatten

    stamp = fetched_at or datetime.now(tz=UTC)
    stats = SyncStats(accounts=len(accounts))
    spans = list(windows(start, end))
    if not spans or not accounts:
        logger.info(
            "sei api: nothing to fetch (%d account(s), %d window(s))", len(accounts), len(spans)
        )
        return [], stats

    total_calls = len(accounts) * len(spans)
    logger.info(
        "sei api: %d account(s) x %d window(s) = %d request(s) at concurrency %d",
        len(accounts),
        len(spans),
        total_calls,
        settings.api_concurrency,
    )

    limit = asyncio.Semaphore(max(1, settings.api_concurrency))
    async with SeiApi(settings) as api:

        async def one(account: AccountRef) -> Any:
            found: list[dict[str, Any]] = []
            for span_start, span_end in spans:
                async with limit:
                    try:
                        page = await api.transactions(account, span_start, span_end, stats=stats)
                    except Exception as exc:
                        stats.failures[f"{account.firm_id}/{account.account_number}"] = str(exc)
                        logger.warning(
                            "sei api: %s/%s %s..%s failed: %s",
                            account.firm_id,
                            account.account_number,
                            span_start,
                            span_end,
                            exc,
                        )
                        continue
                for row in page:
                    row["_firm_id"] = account.firm_id
                    found.append(row)
            stats.done += 1
            stats.rows += len(found)
            # Progress matters on a job measured in hours: without it a stalled run and a
            # slow one look the same.
            if stats.done % 50 == 0 or stats.done == len(accounts):
                logger.info(
                    "sei api: %d/%d accounts, %d rows, %d requests, %d retried, %d failed",
                    stats.done,
                    len(accounts),
                    stats.rows,
                    stats.requests,
                    stats.retries,
                    len(stats.failures),
                )
            return flatten(found, fetched_at=stamp)

        tables = list(await asyncio.gather(*(one(a) for a in accounts)))

    logger.info("sei api: %s", stats.summary)
    return tables, stats


async def fetch_templates(
    settings: SeiSettings, firm_ids: list[str]
) -> tuple[list[dict[str, Any]], SyncStats]:
    """Every firm's client and account enrolment templates.

    One firm's failure is recorded and the rest continue, the same rule
    :func:`fetch_transactions` follows: a firm whose templates could not be read has none this
    run, and the next run re-reads them. The alternative — raising — would mean one
    misconfigured firm blocked template refresh for every other firm in the delivery group.
    """
    stats = SyncStats(accounts=len(firm_ids))
    if not firm_ids:
        logger.info("sei templates: no firms configured")
        return [], stats

    rows: list[dict[str, Any]] = []
    async with SeiApi(settings) as api:
        for firm_id in firm_ids:
            for scope in ("client", "account"):
                stats.requests += 1
                try:
                    found = await api.templates(firm_id, scope=scope)
                except Exception as exc:  # noqa: BLE001 - one firm must not stop the rest
                    stats.failures[f"{firm_id}/{scope}"] = str(exc)[:300]
                    logger.warning("sei templates: firm %s %s failed: %s", firm_id, scope, exc)
                    continue
                for row in found:
                    rows.append({**row, "_firm_id": firm_id, "_scope": scope})
    logger.info(
        "sei templates: %d row(s) over %d firm(s), %d failure(s)",
        len(rows),
        len(firm_ids),
        len(stats.failures),
    )
    return rows, stats


#: SEI's own name for each conformed field. Named EXPLICITLY rather than read through the
#: envelope-guessing `_template_rows`, and for the reason `pkgs/sei` records at length: this
#: response carries TWO collections — `employeeUsersForFirmList` and `metaData` (the paging
#: block, itself a list of one object) — so "the first list-of-objects in the body" returns
#: whichever appears first in key order, and returns the PAGING BLOCK outright when the employee
#: list holds a single non-dict entry. The failure is a picker offering "pageNumber 1,
#: pageSize 100, totalRecords 428" as a person, which reads as an answer rather than as an
#: absence.
EMPLOYEE_USERS_KEY = "employeeUsersForFirmList"
EMPLOYEE_META_KEY = "metaData"


def _employee_rows(body: Any) -> list[dict[str, Any]]:
    """The employee rows, by name, from a body that has two collections in it."""
    if not isinstance(body, dict):
        return []
    found = body.get(EMPLOYEE_USERS_KEY)
    return [row for row in found if isinstance(row, dict)] if isinstance(found, list) else []


def _total_records(body: Any) -> int | None:
    """``metaData.totalRecords``, however SEI wrapped it — an object here, a one-element list in
    the spec's own example. Absent means "stop when a page comes back short", which is the
    behaviour either way; this only lets the sweep know how many to expect."""
    if not isinstance(body, dict):
        return None
    meta = body.get(EMPLOYEE_META_KEY)
    if isinstance(meta, list):
        meta = next((m for m in meta if isinstance(m, dict)), None)
    if not isinstance(meta, dict):
        return None
    total = meta.get("totalRecords")
    return int(total) if isinstance(total, int | str) and str(total).isdigit() else None


def _processing_org_id(body: Any) -> str | None:
    """``processingOrgId`` from firm-default-attr-cache. No firm-id fallback: that is the
    empty-200 that wiped nothing only because the unscoped 400 fired first."""
    cache: Any = None
    if isinstance(body, dict):
        data = body.get("data")
        result = data.get("result") if isinstance(data, dict) else body.get("result")
        if isinstance(result, dict):
            cache = result.get("FIRM_DEFAULT_ATTR_CACHE")
        if cache is None:
            cache = body.get("FIRM_DEFAULT_ATTR_CACHE")
    if not isinstance(cache, dict):
        return None
    value = cache.get("processingOrgId")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def fetch_employee_users(
    settings: SeiSettings, *, firm_ids: list[str] | None = None, max_pages: int = 100
) -> tuple[list[dict[str, Any]], SyncStats]:
    """Every employee user the configured firms' processing org can see, with the email
    the drop lacks.

    ``processingOrgId`` and ``serviceId`` are required on i31. The org is resolved from each
    configured firm's defaults (six Blackfinch-group firms share ``3100``), then one paged
    sweep per distinct org. No firm list is a failure, not an unscoped GET: that 400s,
    and the model's ``replace`` strategy would otherwise empty the relation. The list is the
    Reference report, passed in by the model.

    A page failure stops THAT org rather than the rest: pages are an ordered window over one
    result set, so carrying on past a gap yields a slice nobody can identify, but a second
    processing org is a different result set.
    """
    stats = SyncStats(accounts=0)
    rows: list[dict[str, Any]] = []
    if not settings.api_base_url:
        logger.info("sei employee users: no API base url configured")
        return rows, stats

    firm_ids = list(firm_ids or [])
    if not firm_ids:
        # Named as a failure so ``ref.employee_users`` refuses to replace the table with empty.
        stats.failures["config"] = (
            "the Reference report named no firm; "
            "processingOrgId cannot be resolved from firm defaults"
        )
        logger.warning("sei employee users: no firm list — refusing an unscoped sweep")
        return rows, stats

    service_id = (settings.service_id or "").strip() or DEFAULT_EMPLOYEE_SERVICE_ID
    seen: set[str] = set()

    async with SeiApi(settings) as api:
        orgs: dict[str, list[str]] = {}
        for firm_id in firm_ids:
            stats.requests += 1
            try:
                defaults = await api.firm_default_attributes(firm_id)
            except Exception as exc:  # noqa: BLE001 - one firm must not stop the rest
                stats.failures[f"{firm_id}/processingOrg"] = str(exc)[:300]
                logger.warning("sei employee users: firm %s defaults failed: %s", firm_id, exc)
                continue
            org_id = _processing_org_id(defaults)
            if org_id is None:
                stats.failures[f"{firm_id}/processingOrg"] = (
                    "no processingOrgId in firm-default-attr-cache"
                )
                logger.warning(
                    "sei employee users: firm %s defaults carried no processingOrgId", firm_id
                )
                continue
            orgs.setdefault(org_id, []).append(firm_id)

        for org_id, firms in orgs.items():
            fetched = 0
            for page in range(1, max_pages + 1):
                stats.requests += 1
                try:
                    body = await api.employee_users(
                        processing_org_id=org_id, service_id=service_id, page=page
                    )
                except Exception as exc:  # noqa: BLE001 - a partial sweep beats a failed run
                    stats.failures[f"{org_id}/page/{page}"] = str(exc)[:300]
                    logger.warning(
                        "sei employee users: org %s page %d failed: %s", org_id, page, exc
                    )
                    break
                found = _employee_rows(body)
                fetched += len(found)
                for row in found:
                    uid = row.get("uniqueUserId")
                    key = str(uid).strip() if uid is not None else ""
                    if key:
                        if key in seen:
                            continue
                        seen.add(key)
                    rows.append(row)
                total = _total_records(body)
                if len(found) < MAX_PAGE_SIZE or (total is not None and fetched >= total):
                    break
            else:
                logger.warning(
                    "sei employee users: org %s stopped at the %d-page ceiling",
                    org_id,
                    max_pages,
                )
            logger.info(
                "sei employee users: org %s (%s) %d row(s) this org",
                org_id,
                ",".join(firms),
                fetched,
            )

    stats.accounts = len(rows)
    with_email = sum(1 for r in rows if str(r.get("emailId") or "").strip())
    logger.info(
        "sei employee users: %d row(s), %d with an email, %d failure(s)",
        len(rows),
        with_email,
        len(stats.failures),
    )
    return rows, stats


# What an assignment read is allowed to keep. The rest of the body names the account holder.
_MODEL_FIELDS = (
    "modelId",
    "modelName",
    "modelType",
    "modelTypeDesc",
    "toleranceMultiplier",
    "allowableProcessingSchId",
    "allowableProcessingSchName",
    "investmentObjectiveId",
    "sleeveConfigLevelDesc",
    "status",
)

# The allowance block, and nothing around it. Owner names and bank details stay out of the table.
_ALLOWANCE_FIELDS = (
    "taxYear",
    "isaClientLevelContributionLimit",
    "isaClientLevelYearToDateContribution",
    "isaClientLevelRemainingContribution",
    "jisaClientLevelcontributionlimit",
    "jisaClientLevelYearToDateContribution",
    "jisaClientLevelRemainingContribution",
    "clientYearToDateWithdrawal",
    "baseCurrency",
    "isFlexibleIsaAccount",
    "accountSubscriptionStatus",
)


async def fetch_model_assignments(
    settings: SeiSettings,
    targets: list[tuple[str, str, str, str]],
) -> tuple[list[dict[str, Any]], SyncStats]:
    """One assignment read per distinct model.

    ``targets`` are ``(firm_id, model_id, account_number, portfolio_id)``. A failure
    is recorded and the rest continue: one portfolio SEI will not describe must not
    drop the names already read for the other models.
    """
    stats = SyncStats(accounts=len(targets))
    if not targets:
        return [], stats
    limit = asyncio.Semaphore(max(1, settings.api_concurrency))
    async with SeiApi(settings) as api:

        async def one(target: tuple[str, str, str, str]) -> dict[str, Any] | None:
            firm_id, model_id, account_number, portfolio_id = target
            async with limit:
                try:
                    attrs = await api.model_assignment(firm_id, account_number, portfolio_id)
                except Exception as exc:  # noqa: BLE001 - one portfolio must not fail the catalogue
                    stats.failures[f"{firm_id}/{model_id}"] = str(exc)[:300]
                    logger.warning(
                        "sei models: firm %s model %s failed: %s", firm_id, model_id, exc
                    )
                    return None
            stats.requests += 1
            answered = str(attrs.get("modelId") or "").strip()
            if not answered or answered != model_id:
                stats.failures[f"{firm_id}/{model_id}"] = (
                    f"portfolio answered model {answered or 'none'}"
                )
                logger.warning(
                    "sei models: firm %s model %s portfolio answered %s",
                    firm_id,
                    model_id,
                    answered or "none",
                )
                return None
            row = {key: attrs.get(key) for key in _MODEL_FIELDS}
            row["_firm_id"] = firm_id
            row["_model_id"] = model_id
            return row

        found = await asyncio.gather(*(one(target) for target in targets))
    rows = [row for row in found if row is not None]
    stats.rows = len(rows)
    logger.info("sei models: %d model(s) named, %d failure(s)", len(rows), len(stats.failures))
    return rows, stats


async def fetch_account_allowances(
    settings: SeiSettings,
    accounts: list[AccountRef],
) -> tuple[list[dict[str, Any]], SyncStats]:
    """ISA and JISA contribution figures from account details.

    One account's failure is recorded and the rest continue. The contribution
    block is client-level and the caller decides which side (ISA or JISA) an
    account uses; this keeps both, because the response always carries both.
    """
    stats = SyncStats(accounts=len(accounts))
    if not accounts:
        return [], stats
    limit = asyncio.Semaphore(max(1, settings.api_concurrency))
    async with SeiApi(settings) as api:

        async def one(account: AccountRef) -> dict[str, Any] | None:
            async with limit:
                try:
                    attrs = await api.account_details(account.firm_id, account.account_number)
                except Exception as exc:  # noqa: BLE001 - one account must not drop the others
                    stats.failures[f"{account.firm_id}/{account.account_number}"] = str(exc)[:300]
                    logger.warning(
                        "sei allowances: %s/%s failed: %s",
                        account.firm_id,
                        account.account_number,
                        exc,
                    )
                    return None
            stats.requests += 1
            block = attrs.get("isaJisaContributionDetails")
            if not isinstance(block, dict):
                return None
            row = {key: block.get(key) for key in _ALLOWANCE_FIELDS}
            row["_firm_id"] = account.firm_id
            row["_account_number"] = account.account_number
            return row

        found = await asyncio.gather(*(one(account) for account in accounts))
    rows = [row for row in found if row is not None]
    stats.rows = len(rows)
    logger.info("sei allowances: %d account(s), %d failure(s)", len(rows), len(stats.failures))
    return rows, stats


async def fetch_asset_prices(
    settings: SeiSettings,
    firm_id: str,
    instruments: list[Any],
    start: date,
    end: date,
    *,
    fetched_at: datetime | None = None,
) -> tuple[list[Any], SyncStats]:
    """One request per batch per day from ``start`` through ``end``.

    The call has no range, so a day is its own request. One batch's failure is
    recorded and the rest continue. The caller must not move the scan mark when
    ``stats.failures`` is set.
    """
    from sei_sync.asset_prices import batches, criteria, flatten

    stamp = fetched_at or datetime.now(tz=UTC)
    stats = SyncStats(accounts=len(instruments))
    days: list[date] = []
    day = start
    while day <= end:
        days.append(day)
        day += timedelta(days=1)
    work = [(priced_on, batch) for priced_on in days for batch in batches(instruments)]
    if not work:
        logger.info("sei asset prices: nothing to fetch (%s..%s)", start, end)
        return [], stats
    logger.info(
        "sei asset prices: %d instrument(s) x %d day(s) = %d request(s) at concurrency %d",
        len(instruments),
        len(days),
        len(work),
        settings.api_concurrency,
    )
    limit = asyncio.Semaphore(max(1, settings.api_concurrency))
    # A missing proxy 404s every batch. One answer is enough; the rest of the
    # history would be the same fault.
    stop = asyncio.Event()
    proxy_miss: list[ProxyUnavailable] = []
    async with SeiApi(settings) as api:

        async def one(priced_on: date, batch: list[Any]) -> Any:
            if stop.is_set():
                return None
            async with limit:
                if stop.is_set():
                    return None
                try:
                    found = await api.asset_prices(firm_id, criteria(batch, priced_on))
                except ProxyUnavailable as exc:
                    stop.set()
                    proxy_miss.append(exc)
                    logger.warning("sei asset prices: %s", exc)
                    return None
                except Exception as exc:  # noqa: BLE001 - one batch must not drop the day
                    key = f"{firm_id}/{priced_on.isoformat()}/{batch[0].external_id}"
                    stats.failures[key] = str(exc)[:300]
                    logger.warning("sei asset prices: %s %s failed: %s", firm_id, priced_on, exc)
                    return None
            stats.requests += 1
            lookup = {item.key: item.instrument_id for item in batch}
            by_external = {item.external_id.upper(): item for item in batch}
            rows = [row for row in found if isinstance(row, dict)]
            for row in rows:
                external = str(row.get("externalID") or "").strip().upper()
                item = by_external.get(external)
                if item is not None and not row.get("inputScheme"):
                    row["inputScheme"] = item.scheme
                    row["externalID"] = item.external_id
            table = flatten(
                rows,
                fetched_at=stamp,
                firm_id=firm_id,
                as_of=priced_on,
                instruments=lookup,
            )
            stats.rows += table.num_rows
            return table

        found = await asyncio.gather(*(one(priced_on, batch) for priced_on, batch in work))
    if proxy_miss:
        raise proxy_miss[0]
    return [table for table in found if table is not None], stats
