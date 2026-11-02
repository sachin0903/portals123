"""Deterministic fictional book used by the non-production tenant data tool."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import datalake, tenant_storage
from app.config import settings
from app.providers import custodians

_SEED_VERSION = 4

_PEOPLE = (
    ("Amelia", "Hart", "amelia.hart@example.test", "advised", "Employed", "Architect"),
    ("Noah", "Bennett", "noah.bennett@example.test", "advised", "Self Employed", "Consultant"),
    ("Isla", "Morgan", "isla.morgan@example.test", "execution_only", "Employed", "Teacher"),
    ("Oliver", "Reed", "oliver.reed@example.test", "advised", "Retired", "Engineer"),
    ("Sophia", "Clarke", "sophia.clarke@example.test", "advised", "Employed", "Solicitor"),
    ("George", "Ellis", "george.ellis@example.test", "execution_only", "Employed", "Designer"),
    ("Freya", "Turner", "freya.turner@example.test", "advised", "Self Employed", "Director"),
    ("Arthur", "Shaw", "arthur.shaw@example.test", "advised", "Employed", "Doctor"),
)

# Familiar, active UK transfer counterparties copied from the dev Seccl master-party register on
# 15 September 2026. These are real company names and real provider identifiers — the seed used to
# fabricate transfer credits without seeding the register at all, leaving both the workbench and
# new-transfer picker saying "Unnamed ceding scheme".
_TRANSFER_PROVIDERS: tuple[dict[str, Any], ...] = (
    {
        "name": "AJ Bell Investcentre",
        "masterparty_id": "021F8G1",
        "origo_id": "364",
        "atg_reference": None,
        "products": ["SIPP"],
        "rails": [{"rail": "Origo", "products": ["PENSION"], "reference": "364"}],
    },
    {
        "name": "Aviva",
        "masterparty_id": "021F8HD",
        "origo_id": "27",
        "atg_reference": None,
        "products": ["GIA", "ISA", "SIPP"],
        "rails": [
            {"rail": "Origo", "products": ["PENSION"], "reference": "27"},
            {"rail": "ATG", "products": ["PENSION", "ISA", "GIA"], "reference": "NWBKGB2L"},
        ],
    },
    {
        "name": "Fidelity International",
        "masterparty_id": "021F949",
        "origo_id": "321",
        "atg_reference": "FIDIGB22",
        "products": ["GIA", "ISA", "SIPP"],
        "rails": [
            {"rail": "Origo", "products": ["PENSION"], "reference": "321"},
            {"rail": "ATG", "products": ["PENSION", "ISA", "GIA"], "reference": "FIDIGB22"},
        ],
    },
    {
        "name": "Hargreaves Lansdown",
        "masterparty_id": "021FBJ6",
        "origo_id": "113",
        "atg_reference": "HARLGB21",
        "products": ["SIPP"],
        "rails": [{"rail": "Origo", "products": ["PENSION"], "reference": "3679"}],
    },
    {
        "name": "Scottish Widows",
        "masterparty_id": "021FBHG",
        "origo_id": "38",
        "atg_reference": None,
        "products": ["SIPP"],
        "rails": [{"rail": "Origo", "products": ["PENSION"], "reference": "45"}],
    },
    {
        "name": "Standard Life",
        "masterparty_id": "021FBGC",
        "origo_id": "13",
        "atg_reference": None,
        "products": ["SIPP"],
        "rails": [{"rail": "Origo", "products": ["PENSION"], "reference": "22"}],
    },
)

# The first is the exact anonymised four-account book. The rest are deliberate abstractions of its
# real account/asset shapes at different scales, so demos cover mass-affluent through HNW clients
# without returning to eight copies of the same eight-asset toy portfolio.
_BOOK_VARIANTS: tuple[tuple[tuple[str, Decimal], ...], ...] = (
    (
        ("retirement", Decimal("1")),
        ("medium_term", Decimal("1")),
        ("short_term", Decimal("1")),
        ("international", Decimal("1")),
    ),
    (("retirement", Decimal("0.32")), ("medium_term", Decimal("0.60"))),
    (("short_term", Decimal("0.18")),),
    (("international", Decimal("0.28")), ("short_term", Decimal("0.30"))),
    (("retirement", Decimal("0.12")),),
    (("medium_term", Decimal("0.75")), ("short_term", Decimal("0.09"))),
    (("retirement", Decimal("0.48")), ("international", Decimal("0.08"))),
    (("medium_term", Decimal("0.40")),),
)


@lru_cache(maxsize=1)
def _source_book() -> dict[str, Any]:
    location = Path(__file__).with_name("demo_data") / "client_book.json"
    book: dict[str, Any] = json.loads(location.read_text())
    # The holdings extract has market value and no cost. Stamp a cost/gain pair onto every
    # current holding so the in-memory book — and therefore the lake write — actually carries
    # the figures the overview cards and the Growth column read. Residual £0 rows stay bare.
    for account in book["accounts"]:
        for position in account["positions"]:
            if Decimal(str(position["value"])) == 0:
                continue
            cost, gain, pct = _position_economics(position, account)
            position["book_cost"] = float(cost)
            position["unrealised_gain"] = float(gain)
            position["unrealised_gain_percent"] = float(pct)
    return book


def _book_provider(enabled: list[str]) -> str:
    """The provider the demo book is tagged with.

    Client home and holdings only admit custodian (or ``seeded_demo``) links. Picking the first
    enabled row alphabetically tagged Graphene's book as Morgan Lloyd — a scheme administrator —
    so every wrapper vanished from the client portal beside a staff listing that still showed them.
    Prefer a real custodian when the firm has one; fall back to whatever is enabled so a
    custodian-less tenant can still seed.
    """
    if not enabled:
        raise ValueError("demo seed needs at least one configured and enabled provider")
    wanted = set(enabled)
    for candidate in ("seccl", "sei"):
        if candidate in wanted:
            return candidate
    custody = set(custodians())
    for candidate in enabled:
        if candidate in custody:
            return candidate
    return enabled[0]


async def _preflight(session: AsyncSession, tenant_id: int) -> dict[str, Any]:
    enabled = [
        str(provider)
        for provider in (
            await session.execute(
                text(
                    "SELECT provider::text FROM platform.tenant_platforms "
                    "WHERE tenant_id=:t AND enabled"
                ),
                {"t": tenant_id},
            )
        ).scalars()
    ]
    provider = _book_provider(enabled)
    products = list(
        (
            await session.execute(
                text(
                    "SELECT p.id, p.product_type::text AS product_type, p.name "
                    "FROM platform.products p "
                    "WHERE p.tenant_id=:t "
                    "ORDER BY CASE p.product_type::text "
                    " WHEN 'SIPP' THEN 1 WHEN 'ISA' THEN 2 WHEN 'GIA' THEN 3 ELSE 4 END, p.id"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    by_type = {str(product["product_type"]): dict(product) for product in products}
    required = {account[0] for account in _BOOK_VARIANTS[0]}
    source_types = {
        str(account["product_type"])
        for account in _source_book()["accounts"]
        if str(account["key"]) in required
    }
    missing = sorted(source_types - set(by_type))
    if missing:
        raise ValueError(
            f"demo seed needs these products on the firm's shelf: {', '.join(missing)}"
        )
    adviser = (
        (
            await session.execute(
                text(
                    "SELECT id FROM auth.users WHERE tenant_id=:t AND enabled "
                    "AND ('staff'=ANY(type)) AND is_adviser ORDER BY created_at LIMIT 1"
                ),
                {"t": tenant_id},
            )
        )
        .scalars()
        .first()
    )
    if adviser is None:
        raise ValueError("demo seed needs one enabled staff member marked as an adviser")
    return {"provider": str(provider), "products": by_type, "adviser": adviser}


async def _seed_transfer_providers(session: AsyncSession, tenant_id: int) -> int:
    for provider in _TRANSFER_PROVIDERS:
        await session.execute(
            text(
                "INSERT INTO platform.transfer_providers "
                " (tenant_id,name,masterparty_id,origo_id,atg_reference,status,enabled,"
                "  products,rails,data) "
                "VALUES (:t,:name,:masterparty,:origo,:atg,'Active',true,"
                " CAST(:products AS text[]),CAST(:rails AS jsonb),CAST(:data AS jsonb)) "
                "ON CONFLICT (tenant_id,masterparty_id) WHERE masterparty_id IS NOT NULL "
                "DO UPDATE SET name=EXCLUDED.name,origo_id=EXCLUDED.origo_id,"
                " atg_reference=EXCLUDED.atg_reference,status=EXCLUDED.status,"
                " products=EXCLUDED.products,rails=EXCLUDED.rails"
            ),
            {
                "t": tenant_id,
                "name": provider["name"],
                "masterparty": provider["masterparty_id"],
                "origo": provider["origo_id"],
                "atg": provider["atg_reference"],
                "products": provider["products"],
                "rails": json.dumps(provider["rails"]),
                "data": json.dumps({"seeded_demo": True, "seed_version": _SEED_VERSION}),
            },
        )
    return len(_TRANSFER_PROVIDERS)


def _valuation_series(
    account: dict[str, Any],
    scale: Decimal,
    latest_value: Decimal,
) -> list[dict[str, Any]]:
    """The provider's actual 113-business-day account balances, scaled for abstract clients.

    The closing point is the account's own stated value rather than the scaled daily total. Both
    describe the same day from the same holdings, but one rounds each position and sums while the
    other rounds the sum, so they differ by pennies — and a record whose headline value disagrees
    with the last point of its own chart reads as a reconciliation break rather than as rounding.
    """
    series = [
        {
            "date": point["date"],
            "value": (Decimal(str(point["value"])) * scale).quantize(Decimal("0.01")),
            "cash_value": (Decimal(str(point["cash_value"])) * scale).quantize(Decimal("0.01")),
        }
        for point in account["valuations"]
    ]
    if series:
        series[-1]["value"] = latest_value
    return series


def _held_positions(account: dict[str, Any]) -> list[dict[str, Any]]:
    """Positions the client actually holds.

    The source snapshot keeps residual and dividend-entitlement rows at £0. They are not
    holdings — they have no value, no cost, and no activity — and seeding them produced empty
    rows on the account record next to the real book.
    """
    return [position for position in account["positions"] if Decimal(str(position["value"])) != 0]


def _cumulative_return(account: dict[str, Any]) -> Decimal:
    """The account's stated return, as a fraction, used to back out a cost basis.

    The holdings extract does not carry book cost or gain. Inventing a universal 12% made every
    row look the same; leaving the fields null left the Return column blank. Prefer the
    workbook's cumulative return when it has one. The SIPP's workbook column is empty, so fall
    back to the daily valuation series only when that move stays inside ±100% — the ISA series
    jumps 4× in five months because early snapshots were incomplete, which is not a return.
    """
    for point in reversed(account.get("performance") or []):
        raw = point.get("cumulative_return")
        if raw is not None:
            return Decimal(str(raw)) / Decimal(100)
    valuations = account.get("valuations") or []
    if len(valuations) >= 2:
        start = Decimal(str(valuations[0]["value"]))
        end = Decimal(str(valuations[-1]["value"]))
        if start > 0:
            implied = (end / start) - 1
            if abs(implied) <= 1:
                return implied
    return Decimal("0.12")


def _is_cash_position(position: dict[str, Any]) -> bool:
    return str(position.get("asset_type") or "") == "Cash" or not position.get("isin")


def _position_economics(
    position: dict[str, Any], account: dict[str, Any]
) -> tuple[Decimal, Decimal, Decimal]:
    """Book cost, unrealised gain (pounds), and gain percent for one holding."""
    value = Decimal(str(position["value"]))
    if _is_cash_position(position):
        return value, Decimal("0"), Decimal("0")
    ret = min(max(_cumulative_return(account), Decimal("-0.8")), Decimal("2"))
    cost = (value / (1 + ret)).quantize(Decimal("0.01"))
    if cost <= 0:
        cost = value
    gain = (value - cost).quantize(Decimal("0.01"))
    pct = (gain / cost * 100).quantize(Decimal("0.01")) if cost else Decimal("0")
    return cost, gain, pct


def _activity_key(movement: dict[str, Any]) -> str:
    isin = movement.get("isin")
    if isin:
        return str(isin)
    return f"cash:{movement.get('currency') or 'GBP'}"


def _position_activity_key(position: dict[str, Any]) -> str:
    isin = position.get("isin")
    if isin:
        return str(isin)
    return f"cash:{position.get('currency') or 'GBP'}"


def _opening_movement(position: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    """A buy or deposit so a holding the extract never transacted still has a ledger row."""
    cost, _, _ = _position_economics(position, account)
    cash = _is_cash_position(position)
    opened = str(account.get("opened_at") or "2024-11-11")
    quantity = position.get("quantity")
    price: float | None = 1.0 if cash else None
    if not cash and quantity:
        price = float((cost / Decimal(str(quantity))).quantize(Decimal("0.0001")))
    currency = position.get("currency") or "GBP"
    amount = cost if cash else -cost
    return {
        "key": f"open-{position['key']}",
        "type": "deposit" if cash else "buy",
        "source_type": "Cash Receipt" if cash else "Buy",
        "portfolio_name": None,
        "asset_name": position["name"],
        "isin": position.get("isin"),
        "instrument_key": position["key"],
        "issue_currency": currency,
        "display_price": price,
        "amount": float(amount),
        "transaction_amount": float(amount),
        "transaction_currency": currency,
        "currency": currency,
        "quantity": quantity,
        "price": price,
        "exchange_rate": 1.0,
        "transaction_date": opened,
        "trade_date": opened,
        "settlement_date": opened,
        "reversed": False,
        "provider": None,
        "file_date": opened,
    }


def _activity_for_account(account: dict[str, Any], client_index: int) -> list[dict[str, Any]]:
    """Source movements plus an opening lot for every current holding this seed will not transact.

    The ISA and GIA in the source book have holdings and no transactions at all. Abstract clients
    keep only the latest 36 source rows, which drops the buys that still sit on the SIPP/bond —
    so the opening is decided against the movements we actually keep, not against the full extract.
    """
    source = list(account["transactions"])
    selected = source if client_index == 0 else source[-36:]
    covered = {_activity_key(movement) for movement in selected}
    openings = [
        _opening_movement(position, account)
        for position in _held_positions(account)
        if _position_activity_key(position) not in covered
    ]
    return openings + selected


async def _seed_database(
    session: AsyncSession, tenant_id: int, tenant_uid: str
) -> tuple[dict[str, int], dict[str, list[dict[str, Any]]]]:
    config = await _preflight(session, tenant_id)
    await session.execute(
        text(
            "DELETE FROM documents.documents "
            "WHERE tenant_id = :t AND metadata ->> 'seeded_demo' = 'true'"
        ),
        {"t": tenant_id},
    )
    provider = config["provider"]
    products = config["products"]
    adviser = config["adviser"]
    transfer_provider_count = await _seed_transfer_providers(session, tenant_id)
    source_accounts = {str(account["key"]): account for account in _source_book()["accounts"]}
    lake: dict[str, list[dict[str, Any]]] = {
        "positions": [],
        "instruments": [],
        "valuations": [],
        "transactions": [],
    }
    instruments: dict[str, dict[str, Any]] = {}
    for account in source_accounts.values():
        for position in _held_positions(account):
            instruments.setdefault(
                str(position["key"]),
                {
                    "external_id": position["key"],
                    "name": position["name"],
                    "sedol": position.get("sedol"),
                },
            )
    lake["instruments"] = list(instruments.values())

    contacts: list[int] = []
    account_records: list[tuple[int, int, str]] = []
    transaction_count = valuation_count = 0
    for index, (first, surname, email, service, employment, occupation) in enumerate(_PEOPLE):
        dob = dt.date(1954 + index * 4, (index % 11) + 1, (index * 3 % 27) + 1)
        complete = index != 6
        contact_id = (
            await session.execute(
                text(
                    "INSERT INTO crm.contacts "
                    " (tenant_id,type,service_type,name,first_name,surname,dob,nationality,"
                    "  tax_residency,email,mobile,address,relevant_uk_earnings,gender,"
                    "  marital_status,retirement_age,employment_status,occupation,"
                    "  country_of_birth,source_of_wealth,source_of_funds,employer_name,"
                    "  employer_contributions,data) "
                    "VALUES (:t,'individual',CAST(:service AS crm.service_type),"
                    " :name,:first,:surname,"
                    " :dob,ARRAY['GB'],ARRAY['GB'],:email,:mobile,"
                    " CAST(:address AS jsonb),:earnings,"
                    " :gender,:marital,68,:employment,:occupation,'GB',:wealth,:funds,:employer,"
                    " :employer_contributions,CAST(:data AS jsonb)) RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "service": service,
                    "name": f"{first} {surname}",
                    "first": first,
                    "surname": surname,
                    "dob": dob,
                    "email": email,
                    "mobile": f"+44 7700 900{index:03d}",
                    "address": json.dumps(
                        {
                            "line1": f"{10 + index} Demo Street",
                            "city": "Bristol",
                            "postcode": f"BS1 {index + 1}AA",
                            "country": "GB",
                        }
                    ),
                    "earnings": Decimal(38000 + index * 6500) if complete else None,
                    "gender": "female" if index % 2 == 0 else "male",
                    "marital": "married" if index % 3 else "single",
                    "employment": employment.lower().replace(" ", "_"),
                    "occupation": occupation if complete else None,
                    "wealth": "Salary" if complete else None,
                    "funds": "savings" if complete else None,
                    "employer": f"{surname} Demo Ltd" if employment != "Retired" else None,
                    "employer_contributions": employment != "Retired" if complete else None,
                    "data": json.dumps(
                        {
                            "seeded_demo": True,
                            "seed_version": _SEED_VERSION,
                            "book_shape": (
                                "exact_source_mirror" if index == 0 else "derived_source_mix"
                            ),
                        }
                    ),
                },
            )
        ).scalar_one()
        contacts.append(int(contact_id))
        await session.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id,contact_id,adviser_id,role) "
                "VALUES (:t,:c,:a,'adviser')"
            ),
            {"t": tenant_id, "c": contact_id, "a": adviser},
        )

        variant = _BOOK_VARIANTS[index % len(_BOOK_VARIANTS)]
        for account_index, (source_key, scale) in enumerate(variant):
            source_account = source_accounts[source_key]
            product = products[str(source_account["product_type"])]
            value = sum(
                (
                    (Decimal(str(position["value"])) * scale).quantize(Decimal("0.01"))
                    for position in _held_positions(source_account)
                ),
                Decimal("0"),
            )
            external_id = f"demo-{tenant_uid}-{index + 1}-{source_key}"
            account_id = (
                await session.execute(
                    text(
                        "INSERT INTO platform.accounts "
                        " (tenant_id,contact_id,product_id,status,name,currency,"
                        "  platform_identifier,risk_level,value,performance,opened_at,"
                        "  is_discretionary,custodian,data) "
                        "VALUES (:t,:c,:product,'active',:name,'GBP',:number,:risk,:value,"
                        " CAST(:performance AS jsonb),:opened,:disc,"
                        " CAST(:provider AS platform.provider_code),CAST(:data AS jsonb)) "
                        "RETURNING id"
                    ),
                    {
                        "t": tenant_id,
                        "c": contact_id,
                        "product": product["id"],
                        "name": source_account["name"],
                        "number": f"DEMO-{index + 1:02d}-{source_key.upper().replace('_', '-')}",
                        "risk": (
                            str(source_account["risk_rating"])
                            if source_account.get("risk_rating") is not None
                            else None
                        ),
                        "value": value,
                        "performance": json.dumps(source_account["performance"]),
                        "opened": dt.datetime.combine(
                            dt.date.fromisoformat(str(source_account["opened_at"])),
                            dt.time(),
                            tzinfo=dt.UTC,
                        ),
                        "disc": service == "advised",
                        "provider": provider,
                        "data": json.dumps(
                            {
                                "seeded_demo": True,
                                "seed_version": _SEED_VERSION,
                                "valued_at": max(
                                    str(position["as_of"])
                                    for position in _held_positions(source_account)
                                ),
                                "source_shape": source_key,
                                "source_model_name": source_account.get("model_name"),
                                "source_attributes": source_account.get("attributes", {}),
                                "source_scale": str(scale),
                            }
                        ),
                    },
                )
            ).scalar_one()
            await session.execute(
                text(
                    "INSERT INTO platform.links "
                    " (tenant_id,entity_type,entity_id,provider,link_type,external_id,data) "
                    "VALUES (:t,'account',:entity,CAST(:provider AS platform.provider_code),"
                    " 'primary',:external,CAST(:data AS jsonb))"
                ),
                {
                    "t": tenant_id,
                    "entity": account_id,
                    "provider": provider,
                    "external": external_id,
                    "data": json.dumps({"seeded_demo": True, "seed_version": _SEED_VERSION}),
                },
            )
            account_records.append((int(account_id), int(contact_id), external_id))

            for position in _held_positions(source_account):
                market_value = (Decimal(str(position["value"])) * scale).quantize(Decimal("0.01"))
                quantity = (
                    Decimal(str(position["quantity"])) * scale
                    if position.get("quantity") is not None
                    else None
                )
                cost, gain, gain_pct = _position_economics(position, source_account)
                if market_value == 0:
                    continue
                lake["positions"].append(
                    {
                        "account_external_id": external_id,
                        "instrument_key": position["key"],
                        "asset_name": position["name"],
                        "isin": position.get("isin"),
                        "currency": position.get("currency") or "GBP",
                        "position_type": position["asset_type"],
                        "market_value": float(market_value),
                        "quantity": float(quantity) if quantity is not None else None,
                        "price": position.get("price"),
                        # The holdings extract does not carry cost or gain. Back them out from
                        # the account's stated return so the Return column has a number, and
                        # always as floats so the lake table infers a numeric column rather
                        # than a null-type one that the demo reader then cannot select.
                        "book_cost": float((cost * scale).quantize(Decimal("0.01"))),
                        "unrealised_gain": float((gain * scale).quantize(Decimal("0.01"))),
                        "unrealised_gain_percent": float(gain_pct),
                        "allocation": position["allocation"],
                        "as_of_date": position["as_of"],
                    }
                )

            for point in _valuation_series(source_account, scale, value):
                as_of = dt.date.fromisoformat(str(point["date"]))
                market = Decimal(str(point["value"]))
                cash = Decimal(str(point["cash_value"]))
                await session.execute(
                    text(
                        "INSERT INTO platform.valuations "
                        " (tenant_id,account_id,as_of_date,source_system,market_value,cash_value,"
                        "  currency,data) "
                        "VALUES (:t,:a,:day,CAST(:provider AS platform.provider_code),"
                        " :market,:cash,'GBP',CAST(:data AS jsonb))"
                    ),
                    {
                        "t": tenant_id,
                        "a": account_id,
                        "day": as_of,
                        "provider": provider,
                        "market": market,
                        "cash": cash,
                        "data": json.dumps(
                            {
                                "seeded_demo": True,
                                "seed_version": _SEED_VERSION,
                                "series_kind": "provider_balance",
                            }
                        ),
                    },
                )
                lake["valuations"].append(
                    {
                        "account_external_id": external_id,
                        "as_of_date": as_of,
                        "market_value": float(market),
                    }
                )
                valuation_count += 1

            # The mirror keeps every deduplicated source transaction, plus an opening lot for
            # each current holding the extract never transacted (the ISA and GIA have none).
            # Abstract clients keep those openings and the latest 36 source rows so the ledger
            # still names every holding without multiplying a 471-row source seven more times.
            # Monetary amounts and quantities follow the same scale as their account; prices
            # and returns do not.
            for movement in _activity_for_account(source_account, index):
                kind = str(movement["type"])
                # "Free Receipt" is a provider transaction type, not proof of a pension-transfer
                # saga. On the source SIPP it is a transfer in; on the offshore bond it is an
                # unpriced security movement and remains searchable under its exact source label,
                # but must not populate the Transfers workbench.
                if kind == "transfer_in" and (
                    index == 0 or source_account["product_type"] != "SIPP"
                ):
                    kind = "adjustment"
                day = dt.date.fromisoformat(str(movement["transaction_date"]))
                amount = (Decimal(str(movement.get("amount") or 0)) * scale).quantize(
                    Decimal("0.0001")
                )
                quantity = (
                    Decimal(str(movement["quantity"])) * scale
                    if movement.get("quantity") is not None
                    else None
                )
                source_id = f"{external_id}-tx-{movement['key']}"
                transaction_data: dict[str, object] = {
                    "seeded_demo": True,
                    "seed_version": _SEED_VERSION,
                    "provider": movement.get("provider"),
                    "portfolio_name": movement.get("portfolio_name"),
                    "issue_currency": movement.get("issue_currency"),
                    "display_price": movement.get("display_price"),
                    "transaction_amount": movement.get("transaction_amount"),
                    "transaction_currency": movement.get("transaction_currency"),
                    "exchange_rate": movement.get("exchange_rate"),
                    "trade_date": movement.get("trade_date"),
                    "file_date": movement.get("file_date"),
                    "reversed": movement.get("reversed", False),
                }
                if kind == "transfer_in":
                    ceding = _TRANSFER_PROVIDERS[(index + account_index) % len(_TRANSFER_PROVIDERS)]
                    transaction_data.update(
                        {
                            "previous_provider": ceding["masterparty_id"],
                            "previous_provider_name": ceding["name"],
                            "transfer_status": "Completed",
                            "is_pension_transfer": True,
                            "transfer_value": str(
                                (value * Decimal("0.72")).quantize(Decimal("0.01"))
                            ),
                        }
                    )
                await session.execute(
                    text(
                        "INSERT INTO platform.transactions "
                        " (tenant_id,account_id,contact_id,source_system,"
                        "  source_transaction_id,type,"
                        "  source_type,isin,asset_name,amount,currency,quantity,price,"
                        "  settlement_amount,settlement_currency,transaction_date,settlement_date,"
                        "  posted_at,status,data) "
                        "VALUES (:t,:a,:c,CAST(:provider AS platform.provider_code),:source,"
                        " CAST(:kind AS platform.transaction_type),:source_type,:isin,"
                        " :asset,:amount,:currency,:quantity,:price,:amount,:currency,:day,"
                        " :settled,:posted,:status,CAST(:data AS jsonb))"
                    ),
                    {
                        "t": tenant_id,
                        "a": account_id,
                        "c": contact_id,
                        "provider": provider,
                        "source": source_id,
                        "kind": kind,
                        "source_type": movement["source_type"],
                        "isin": movement.get("isin"),
                        "asset": movement.get("asset_name"),
                        "amount": amount,
                        "currency": movement.get("currency") or "GBP",
                        "quantity": quantity,
                        "price": movement.get("price"),
                        "day": day,
                        "settled": movement.get("settlement_date"),
                        "posted": dt.datetime.combine(day, dt.time(12), tzinfo=dt.UTC),
                        "status": "reversed" if movement.get("reversed") else "completed",
                        "data": json.dumps(transaction_data),
                    },
                )
                lake["transactions"].append(
                    {
                        "account_external_id": external_id,
                        "transaction_date": day,
                        "source_type": movement["source_type"],
                        "source_subtype": None,
                        "type": kind,
                        "instrument_key": movement.get("isin") or movement.get("instrument_key"),
                        "isin": movement.get("isin"),
                        "net_amount": float(amount),
                        "gross_amount": float(amount),
                        "quantity": float(quantity) if quantity is not None else None,
                        "unit_price": movement.get("price"),
                        "currency": movement.get("currency") or "GBP",
                        "status": "reversed" if movement.get("reversed") else "completed",
                    }
                )
                transaction_count += 1

    await _seed_journeys(session, tenant_id, contacts[:3], account_records, adviser, provider)
    return (
        {
            "clients": len(contacts),
            "accounts": len(account_records),
            "transactions": transaction_count,
            "valuations": valuation_count,
            "positions": len(lake["positions"]),
            "transfer_providers": transfer_provider_count,
        },
        lake,
    )


_FORM_CODES = {
    "core": "applicant_core",
    "additional_info": "applicant_offshore",
    "account_preferences": "account_preferences",
    "pension": "applicant_pension",
    "employer": "applicant_employer",
}
_TERM_CODES = {
    "terms_general": "general_declaration",
    "terms_platform": "platform_terms",
    "terms_isa": "isa_declaration",
    "terms_transfer": "transfer_declaration",
}


async def _record_completed_application(
    session: AsyncSession, tenant_id: int, execution_id: int, adviser: Any
) -> None:
    """Write the finished onboarding's answers, declarations and signature.

    The listing stamp alone leaves every step pending, so the application PDF has nothing to
    show. This records the client's own details on the forms, the terms they agreed, and marks
    the steps done.
    """
    from app.ids import new_id

    contact = (
        (
            await session.execute(
                text(
                    "SELECT ct.id, ct.first_name, ct.surname, ct.dob, ct.gender, ct.email, "
                    "       ct.mobile AS phone, ct.nationality, ct.tax_residency, "
                    "       ct.address, ct.occupation, ct.country_of_birth, "
                    "       ct.service_type::text AS service_type "
                    "FROM flows.cases c "
                    "JOIN crm.contacts ct ON ct.id = c.subject_id AND c.subject_type = 'contact' "
                    "WHERE c.execution_id = :e AND c.tenant_id = :t AND c.kind = 'profile' "
                    "ORDER BY c.subject_id LIMIT 1"
                ),
                {"e": execution_id, "t": tenant_id},
            )
        )
        .mappings()
        .one()
    )
    account = (
        (
            await session.execute(
                text(
                    "SELECT acct.id, acct.name, acct.currency, "
                    "       p.product_type::text AS product_type "
                    "FROM flows.cases c "
                    "JOIN platform.accounts acct "
                    "  ON acct.id = c.subject_id AND c.subject_type = 'account' "
                    "LEFT JOIN platform.products p ON p.id = acct.product_id "
                    "WHERE c.execution_id = :e AND c.tenant_id = :t AND c.kind = 'account' "
                    "ORDER BY acct.id LIMIT 1"
                ),
                {"e": execution_id, "t": tenant_id},
            )
        )
        .mappings()
        .one()
    )
    title = "Ms" if contact["gender"] == "female" else "Mr"
    capacity = "execution_only" if contact["service_type"] == "execution_only" else "discretionary"
    bank = {
        "name": f"{contact['first_name']} {contact['surname']}",
        "sort_code": "20-00-00",
        "account_number": "55779911",
    }
    await session.execute(
        text(
            "UPDATE crm.contacts SET title = :title, "
            "  national_identifier = :ni, national_identifier_type = 'NINO', "
            "  bank_account = CAST(:bank AS jsonb), "
            "  occupation = COALESCE(occupation, 'Teacher'), "
            "  source_of_wealth = COALESCE(source_of_wealth, 'Salary'), "
            "  source_of_funds = COALESCE(source_of_funds, 'savings') "
            "WHERE id = :id AND tenant_id = :t"
        ),
        {
            "title": title,
            "ni": f"JX{int(contact['id']) % 1000000:06d}C",
            "bank": json.dumps(bank),
            "id": contact["id"],
            "t": tenant_id,
        },
    )
    pension = account["product_type"] == "SIPP"
    await session.execute(
        text(
            "UPDATE platform.accounts SET investment_capacity = :capacity, "
            "  dividend_treatment = :dividend "
            "WHERE id = :id AND tenant_id = :t"
        ),
        {
            "capacity": capacity,
            "dividend": None if pension else "reinvest",
            "id": account["id"],
            "t": tenant_id,
        },
    )

    request_id = (
        await session.execute(
            text(
                "SELECT ri.request_id FROM flows.case_steps cs "
                "JOIN flows.cases c ON c.id = cs.case_id "
                "JOIN documents.request_items ri ON ri.id = cs.request_item_id "
                "WHERE c.execution_id = :e AND c.tenant_id = :t LIMIT 1"
            ),
            {"e": execution_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if request_id is None:
        request_id = (
            await session.execute(
                text(
                    "INSERT INTO documents.requests "
                    "  (tenant_id, contact_id, requested_by, status, completed_at) "
                    "VALUES (:t, :c, :by, 'fulfilled', now()) RETURNING id"
                ),
                {"t": tenant_id, "c": contact["id"], "by": adviser},
            )
        ).scalar_one()

    forms = {
        str(row["code"]): row
        for row in (
            await session.execute(
                text(
                    "SELECT id, uid, code FROM documents.forms "
                    "WHERE tenant_id = :t AND code = ANY(:codes)"
                ),
                {"t": tenant_id, "codes": list(_FORM_CODES.values())},
            )
        )
        .mappings()
        .all()
    }
    address = contact["address"] if isinstance(contact["address"], dict) else {}
    dob = contact["dob"].isoformat() if contact["dob"] else None
    core_answers = {
        "title": title,
        "first_name": contact["first_name"],
        "surname": contact["surname"],
        "dob": dob,
        "gender": contact["gender"],
        "nationality": list(contact["nationality"] or ["GB"]),
        "tax_residency": list(contact["tax_residency"] or ["GB"]),
        "national_identifier": f"JX{int(contact['id']) % 1000000:06d}C",
        "national_identifier_type": "NINO",
        "email": contact["email"],
        "phone": contact["phone"],
        "address": address,
        "bank_style": "uk",
        "bank_account.account_name": bank["name"],
        "bank_account.sort_code": bank["sort_code"],
        "bank_account.account_number": bank["account_number"],
    }
    additional_answers = {
        "country_of_birth": contact["country_of_birth"] or "GB",
        "town_of_birth": (address or {}).get("city") or "Bristol",
        "occupation": contact["occupation"] or "Teacher",
        "source_of_funds": "Salary or employment income",
        "source_of_wealth": "Salary",
    }
    account_answers = {
        "account_name": account["name"],
        "currency": account["currency"] or "GBP",
        "investment_capacity": capacity,
    }
    if not pension:
        account_answers["dividend_treatment"] = "reinvest"
    answers_for = {
        "core": core_answers,
        "additional_info": additional_answers,
        "account_preferences": account_answers,
    }

    steps = (
        (
            await session.execute(
                text(
                    "SELECT cs.id, cs.step_key, cs.step_type, cs.title, cs.request_item_id "
                    "FROM flows.case_steps cs "
                    "JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE c.execution_id = :e AND c.tenant_id = :t "
                    "  AND cs.status NOT IN ('skipped', 'cancelled')"
                ),
                {"e": execution_id, "t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    for step in steps:
        key = str(step["step_key"] or "")
        if step["step_type"] == "fill_form" and key in answers_for:
            form = forms.get(_FORM_CODES[key])
            if form is None:
                continue
            item_id = step["request_item_id"]
            if item_id is None:
                item_id = (
                    await session.execute(
                        text(
                            "INSERT INTO documents.request_items "
                            "  (request_id, tenant_id, action, title, form_uid, status, "
                            "   fulfilled_at) "
                            "VALUES (:r, :t, 'complete_form', :title, :form, 'fulfilled', now()) "
                            "RETURNING id"
                        ),
                        {
                            "r": request_id,
                            "t": tenant_id,
                            "title": step["title"] or "Details",
                            "form": form["uid"],
                        },
                    )
                ).scalar_one()
                await session.execute(
                    text("UPDATE flows.case_steps SET request_item_id = :i WHERE id = :id"),
                    {"i": item_id, "id": step["id"]},
                )
            else:
                await session.execute(
                    text(
                        "UPDATE documents.request_items SET form_uid = :form, "
                        "  status = 'fulfilled', fulfilled_at = now() WHERE id = :id"
                    ),
                    {"form": form["uid"], "id": item_id},
                )
            await session.execute(
                text(
                    "INSERT INTO documents.form_responses "
                    "  (tenant_id, request_item_id, form_uid, form_id, contact_id, "
                    "   submitted_by, answers) "
                    "VALUES (:t, :item, :uid, :form, :contact, :by, CAST(:answers AS jsonb))"
                ),
                {
                    "t": tenant_id,
                    "item": item_id,
                    "uid": form["uid"],
                    "form": form["id"],
                    "contact": contact["id"],
                    "by": adviser,
                    "answers": json.dumps(answers_for[key]),
                },
            )
        elif step["step_type"] == "accept_terms" and key in _TERM_CODES:
            term = (
                (
                    await session.execute(
                        text(
                            "SELECT t.id, t.uid, v.id AS version_id, v.title, v.body_html "
                            "FROM terms.terms t "
                            "JOIN terms.term_versions v ON v.id = t.current_published_version_id "
                            "WHERE t.tenant_id = :t AND t.code = :code"
                        ),
                        {"t": tenant_id, "code": _TERM_CODES[key]},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if term is None or not term["body_html"]:
                continue
            document_id = (
                await session.execute(
                    text(
                        "INSERT INTO documents.documents "
                        "  (uid, tenant_id, document_type, source, name, status, metadata) "
                        "VALUES (:u, :t, 'terms', 'generated', :n, 'generated', "
                        "        CAST(:m AS jsonb)) "
                        "RETURNING id"
                    ),
                    {
                        "u": new_id(),
                        "t": tenant_id,
                        "n": term["title"],
                        "m": json.dumps(
                            {
                                "content": term["body_html"],
                                "audience": "client",
                                "term_uid": term["uid"],
                                "seeded_demo": True,
                            }
                        ),
                    },
                )
            ).scalar_one()
            item_id = (
                await session.execute(
                    text(
                        "INSERT INTO documents.request_items "
                        "  (request_id, tenant_id, action, title, target_document_id, "
                        "   status, fulfilled_at) "
                        "VALUES (:r, :t, 'acknowledge', :title, :doc, 'fulfilled', now()) "
                        "RETURNING id"
                    ),
                    {
                        "r": request_id,
                        "t": tenant_id,
                        "title": term["title"],
                        "doc": document_id,
                    },
                )
            ).scalar_one()
            await session.execute(
                text("UPDATE flows.case_steps SET request_item_id = :i WHERE id = :id"),
                {"i": item_id, "id": step["id"]},
            )
            await session.execute(
                text(
                    "INSERT INTO terms.acceptances "
                    "  (uid, tenant_id, contact_id, term_id, term_version_id, "
                    "   accepted_by, channel) "
                    "VALUES (:u, :t, :c, :term, :version, :by, "
                    "        CAST('portal' AS terms.channel)) "
                    "ON CONFLICT (contact_id, term_version_id) "
                    "  WHERE contact_id IS NOT NULL DO NOTHING"
                ),
                {
                    "u": new_id(),
                    "t": tenant_id,
                    "c": contact["id"],
                    "term": term["id"],
                    "version": term["version_id"],
                    "by": adviser,
                },
            )

    await session.execute(
        text(
            "UPDATE flows.case_steps cs SET status = 'fulfilled', completed_at = now() "
            "FROM flows.cases c "
            "WHERE cs.case_id = c.id AND c.execution_id = :e AND c.tenant_id = :t "
            "  AND cs.status NOT IN ('skipped', 'cancelled')"
        ),
        {"e": execution_id, "t": tenant_id},
    )
    await session.execute(
        text(
            "UPDATE documents.requests SET status = 'fulfilled', completed_at = now() "
            "WHERE id = :id"
        ),
        {"id": request_id},
    )


async def _seed_journeys(
    session: AsyncSession,
    tenant_id: int,
    contacts: list[int],
    accounts: list[tuple[int, int, str]],
    adviser: Any,
    provider: str,
) -> None:
    from app.flows import orchestrator
    from app.flows.planning import AccountSpec, ApplicationGraph, Participant

    flow = (
        (
            await session.execute(
                text(
                    "SELECT f.id, f.current_published_version_id, v.definition "
                    "FROM flows.flows f JOIN flows.flow_versions v "
                    " ON v.id=f.current_published_version_id "
                    "WHERE f.tenant_id=:t AND f.code='onboarding'"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if flow is None:
        raise ValueError("demo seed needs the published onboarding flow")
    states = ("draft", "active", "completed")
    for index, contact_id in enumerate(contacts):
        account_id = next(a for a, c, _ in accounts if c == contact_id)
        application_id = await _create_demo_application(session, tenant_id, provider)
        await session.execute(
            text("UPDATE platform.accounts SET application_id=:app WHERE id=:account"),
            {"app": application_id, "account": account_id},
        )
        await session.execute(
            text(
                "INSERT INTO crm.account_contacts "
                " (tenant_id,application_id,account_id,contact_id,role) "
                "VALUES (:t,:app,:account,:contact,CAST('owner' AS crm.account_role))"
            ),
            {
                "t": tenant_id,
                "app": application_id,
                "account": account_id,
                "contact": contact_id,
            },
        )
        graph = ApplicationGraph(
            participants=(Participant(contact_id, "individual", frozenset({"owner"})),),
            accounts=(AccountSpec(account_id, (contact_id,)),),
        )
        created = await orchestrator.start_execution(
            session,
            tenant_id=tenant_id,
            flow_id=int(flow["id"]),
            flow_version_id=int(flow["current_published_version_id"]),
            definition=dict(flow["definition"]),
            graph=graph,
            application_id=int(application_id),
            created_by=str(adviser),
            data={"seeded_demo": True, "adviser_declared": True},
        )
        # Cases and steps are created by the real orchestrator.  These terminal
        # stamps provide deterministic listing examples without dispatching real
        # client actions or emails from a fixture.
        await session.execute(
            text(
                "UPDATE flows.executions SET status=:status, "
                " completed_at=CASE WHEN :status='completed' THEN now() ELSE NULL END "
                "WHERE id=:id"
            ),
            {"status": states[index], "id": created["id"]},
        )
        await session.execute(
            text("UPDATE crm.applications SET status=:status WHERE id=:id"),
            {"status": states[index], "id": application_id},
        )
        if states[index] == "completed" and isinstance(created["id"], int):
            await _record_completed_application(session, tenant_id, created["id"], adviser)


async def _create_demo_application(session: AsyncSession, tenant_id: int, provider: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO crm.applications (tenant_id,provider,status,custody) "
                    "VALUES (:t,CAST(:provider AS platform.provider_code),'draft','onshore') "
                    "RETURNING id"
                ),
                {"t": tenant_id, "provider": provider},
            )
        ).scalar_one()
    )


def _writer_dsn() -> str:
    value = settings.datalake_database_url
    if not value:
        raise ValueError("DATALAKE_DATABASE_URL is required to seed demo holdings")
    return value.replace("postgresql+psycopg://", "postgresql://")


def _write_lake(
    uid: str,
    store: tenant_storage.TenantStore,
    rows: dict[str, list[dict[str, Any]]],
) -> None:
    import duckdb
    import pyarrow as pa

    from app.lake.attach import attach_ducklake_sql, s3_secret_sql

    conn = duckdb.connect(":memory:")
    try:
        for extension in ("ducklake", "postgres", "httpfs"):
            conn.execute(f"INSTALL {extension}; LOAD {extension}")
        conn.execute(
            s3_secret_sql(
                "demo_lake_s3",
                key_id=settings.s3_access_key_id,
                secret=settings.s3_secret_access_key,
                scope=f"s3://{store.bucket}",
                endpoint=store.endpoint,
                region=store.region or "",
            )
        )
        conn.execute(
            attach_ducklake_sql(
                "lake",
                _writer_dsn(),
                metadata_schema=uid,
                data_path=f"s3://{store.bucket}/datalake/",
                read_only=False,
            )
        )
        conn.execute("CREATE SCHEMA IF NOT EXISTS lake.platform")
        for relation, relation_rows in rows.items():
            table = pa.Table.from_pylist(relation_rows)
            name = f"seed_{relation}"
            conn.register(name, table)
            # DROP then CREATE, not CREATE OR REPLACE. DuckLake keeps a column that was first
            # inferred as NULL-typed (a seed that wrote the keys as None) across REPLACE, so
            # later floats land as SQL NULL — book cost dashes and Growth +£0.00 beside a
            # working Cash figure, because `market_value` was numeric from day one.
            conn.execute(f"DROP TABLE IF EXISTS lake.platform.{relation}")  # noqa: S608 - fixed mapping
            conn.execute(
                f"CREATE TABLE lake.platform.{relation} AS SELECT * FROM {name}"  # noqa: S608 - fixed mapping
            )
            conn.unregister(name)
    finally:
        conn.close()


async def seed(
    maker: async_sessionmaker[AsyncSession], tenant_id: int, tenant_uid: str
) -> dict[str, int]:
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        counts, lake_rows = await _seed_database(session, tenant_id, tenant_uid)
        store = await tenant_storage.get_tenant_store(session, tenant_id)
        if store is None:
            raise ValueError("demo seed needs active tenant storage")
        # Keep the OLTP transaction open until the lake write succeeds. If the
        # external write fails, the book rolls back; if the process dies after
        # the lake succeeds, a retry replaces the same deterministic relations.
        # The writer attach creates the catalogue schema and does not grant
        # lake_reader. Provisioning does that grant only when the writer URL is
        # already set, so a seed has to grant it itself or the portal reads an
        # empty book beside the account values.
        await datalake.ensure_tenant_catalog(tenant_uid)
        await asyncio.to_thread(_write_lake, tenant_uid, store, lake_rows)
    from app.lake import evict_tenant

    # Pooled read-only attaches still see the pre-DROP snapshot until they are closed.
    await evict_tenant(tenant_uid)
    return counts
