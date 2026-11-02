"""Money actions on baton: transaction.payment reads the book then runs the Seccl money leaf, which
adapts the existing idempotency-aware handler. A refused send parks as NeedsAttention; an already
sent one is Skipped. The underlying handler is faked, so no instruction setup is needed."""

from __future__ import annotations

import uuid

import pytest
from baton import Engine, Subject
from baton.registry import default_registry
from baton.store.memory import FakeClock
from baton.store.postgres import PostgresStore
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.actions.defs  # noqa: F401 - registers the migrated actions
from app.actions.attempt import Attempt, OperationError
from app.actions.hooks import GrapheneHooks
from app.actions.redact import firm_visible

pytestmark = pytest.mark.asyncio


def _engine(url: str) -> Engine:
    eng = create_async_engine(url)
    hooks = GrapheneHooks()
    store = PostgresStore(eng, schema="baton", hooks=hooks, clock=FakeClock())
    return Engine(
        store, default_registry, hooks=hooks, redact=firm_visible, default_budget_seconds=1000
    )


async def _seed_account(url: str, *, custodian: str = "seccl") -> tuple[int, int, str]:
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    n = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tid = (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES "
                    "('ten_' || :n, 'mny-' || :n, 'Money Firm') RETURNING id"
                ),
                {"n": n},
            )
        ).scalar_one()
        cid = (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname) "
                    "VALUES (:t, :u, 'individual', 'Ada', 'Okonkwo') RETURNING id"
                ),
                {"t": tid, "u": f"con_{n}"},
            )
        ).scalar_one()
        aid = (
            await s.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, uid, contact_id, custodian, status) "
                    "VALUES (:t, :u, :c, CAST(:cust AS platform.provider_code), 'active') "
                    "RETURNING id"
                ),
                {"t": tid, "u": f"acc_{n}", "c": cid, "cust": custodian},
            )
        ).scalar_one()
    await eng.dispose()
    return int(tid), int(aid), f"acc_{n}"


async def _start_advance(engine, url, action_key, *, tid, aid, auid, key, **params):
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sr = await engine.start(
            engine.store.bind(s),
            engine.registry.require(action_key),
            tenant_id=tid,
            params={"account_id": aid, **params},
            idempotency_key=key,
            subjects=[Subject("account", auid, aid)],
        )
    await eng.dispose()
    return sr, await engine.advance(sr.run.uid)


async def _lodge(engine, url, tid, key, uid="C-1"):
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sr = await engine.start(
            engine.store.bind(s),
            engine.registry.require("contribution.lodge"),
            tenant_id=tid,
            params={"uid": uid},
            idempotency_key=key,
            subjects=[Subject("contribution", uid, None)],
        )
    await eng.dispose()
    return sr, await engine.advance(sr.run.uid)


async def test_contribution_lodge_instructed_completes(db, monkeypatch):
    tid, _aid, _auid = await _seed_account(db["app"])
    engine = _engine(db["app"])

    async def fake(session, *, payload, subjects):
        return Attempt(
            ok=True,
            method="POST",
            url="contribution/lodge",
            response={"status": "instructed", "sei_id": "ACR-1"},
        )

    monkeypatch.setattr("app.actions.money.lodge_contribution", fake)
    _sr, res = await _lodge(engine, db["app"], tid, "clodge1")
    assert res.outcome == "completed", res.outcome
    assert res.run.result == {"status": "instructed", "sei_id": "ACR-1"}


async def test_contribution_lodge_skipped_is_not_a_failure(db, monkeypatch):
    tid, _aid, _auid = await _seed_account(db["app"])
    engine = _engine(db["app"])

    async def fake(session, *, payload, subjects):
        return Attempt(
            ok=False,
            method="POST",
            url="contribution/lodge",
            response={"status": "skipped", "detail": "No contribution lodge for custodian seccl."},
        )

    monkeypatch.setattr("app.actions.money.lodge_contribution", fake)
    sr, res = await _lodge(engine, db["app"], tid, "clodge2")
    assert res.outcome == "completed"
    assert res.run.state.name == "Skipped"


async def test_contribution_lodge_failure_parks_as_needs_attention(db, monkeypatch):
    tid, _aid, _auid = await _seed_account(db["app"])
    engine = _engine(db["app"])

    async def fake(session, *, payload, subjects):
        return Attempt(
            ok=False,
            method="POST",
            url="contribution/lodge",
            response={"status": "blocked", "detail": "SEI needs the client's NI number."},
            error="SEI needs the client's NI number.",
        )

    monkeypatch.setattr("app.actions.money.lodge_contribution", fake)
    sr, res = await _lodge(engine, db["app"], tid, "clodge3")
    assert res.outcome == "failed"
    assert res.run.state.name == "NeedsAttention"


async def test_payment_sends_through_the_seccl_leaf(db, monkeypatch):
    tid, aid, auid = await _seed_account(db["app"])
    engine = _engine(db["app"])

    async def fake(session, *, payload, subjects):
        return Attempt(
            ok=True,
            method="POST",
            url="/portfoliotransactiongroup",
            response={"id": "PTG-1", "status": "submitted"},
        )

    monkeypatch.setattr("app.integrations.seccl.actions.seccl_payment", fake)
    sr, res = await _start_advance(
        engine, db["app"], "transaction.payment", tid=tid, aid=aid, auid=auid, key="pay1"
    )
    assert res.outcome == "completed", res.outcome
    assert res.run.result == {"id": "PTG-1", "status": "submitted"}
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    assert [c.run.action_key for c in detail.children] == ["load.account_book", "seccl.payment"]


async def test_a_refused_payment_parks_as_needs_attention(db, monkeypatch):
    tid, aid, auid = await _seed_account(db["app"])
    engine = _engine(db["app"])

    async def fake(session, *, payload, subjects):
        return Attempt(
            ok=False,
            method="POST",
            url="/portfoliotransactiongroup",
            response={"error": "insufficient cash"},
            error="insufficient cash",
        )

    monkeypatch.setattr("app.integrations.seccl.actions.seccl_payment", fake)
    sr, res = await _start_advance(
        engine, db["app"], "transaction.payment", tid=tid, aid=aid, auid=auid, key="pay2"
    )
    assert res.outcome == "failed"
    assert res.run.state.name == "NeedsAttention"
    # the refusal is recorded on the leaf's attempt
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    leaf = detail.children[1]
    assert leaf.run.action_key == "seccl.payment"
    assert leaf.attempts[0].response == {"error": "insufficient cash"}


async def test_an_already_sent_payment_is_skipped(db, monkeypatch):
    tid, aid, auid = await _seed_account(db["app"])
    engine = _engine(db["app"])

    async def fake(session, *, payload, subjects):
        return Attempt(ok=True, skipped=True, response={"skipped": "already instructed"})

    monkeypatch.setattr("app.integrations.seccl.actions.seccl_payment", fake)
    sr, res = await _start_advance(
        engine, db["app"], "transaction.payment", tid=tid, aid=aid, auid=auid, key="pay3"
    )
    assert res.outcome == "completed"
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    assert detail.children[1].run.state.name == "Skipped"


async def test_a_missing_booked_row_is_permanent(db, monkeypatch):

    tid, aid, auid = await _seed_account(db["app"])
    engine = _engine(db["app"])

    async def fake(session, *, payload, subjects):
        raise OperationError("Book the payment on the account first.", permanent=True)

    monkeypatch.setattr("app.integrations.seccl.actions.seccl_payment", fake)
    sr, res = await _start_advance(
        engine, db["app"], "transaction.payment", tid=tid, aid=aid, auid=auid, key="pay4"
    )
    assert res.outcome == "failed"
    # the composite surfaces the leaf's permanent refusal
    assert res.run.state.name == "NeedsAttention"


async def test_transfer_create_places_through_the_seccl_leaf(db, monkeypatch):
    tid, aid, auid = await _seed_account(db["app"])
    engine = _engine(db["app"])

    async def fake(session, *, payload, subjects):
        return Attempt(
            ok=True,
            method="POST",
            url="/portfoliotransactiongroup",
            response={"seccl_placed": True, "status": "requested"},
        )

    monkeypatch.setattr("app.integrations.seccl.actions.seccl_transfer", fake)
    sr, res = await _start_advance(
        engine, db["app"], "transaction.transfer", tid=tid, aid=aid, auid=auid, key="tr1"
    )
    assert res.outcome == "completed", res.outcome
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    assert [c.run.action_key for c in detail.children] == ["load.account_book", "seccl.transfer"]


async def test_transfer_create_dispatches_to_the_sei_leaf(db, monkeypatch):
    tid, aid, auid = await _seed_account(db["app"], custodian="sei")
    engine = _engine(db["app"])

    async def fake(session, *, payload, subjects):
        return Attempt(
            ok=True,
            method="POST",
            url="/transfers",
            response={"sei_raised": True, "status": "requested"},
        )

    monkeypatch.setattr("app.integrations.sei.actions.sei_transfer", fake)
    sr, res = await _start_advance(
        engine, db["app"], "transaction.transfer", tid=tid, aid=aid, auid=auid, key="tr-sei"
    )
    assert res.outcome == "completed", res.outcome
    assert res.run.result == {"sei_raised": True, "status": "requested"}
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    assert [c.run.action_key for c in detail.children] == ["load.account_book", "sei.transfer"]


async def test_payment_dispatches_to_the_sei_leaf(db, monkeypatch):
    # The reported bug: a payment on a SEI account made no SEI call because _send only wired Seccl.
    tid, aid, auid = await _seed_account(db["app"], custodian="sei")
    engine = _engine(db["app"])

    async def fake(session, *, payload, subjects):
        return Attempt(
            ok=True,
            method="POST",
            url="/currencyreceiptservices/createcurrencyreceipt",
            response={"sei_lodged": True, "activity_id": "ACR-9"},
        )

    monkeypatch.setattr("app.integrations.sei.actions.sei_payment", fake)
    sr, res = await _start_advance(
        engine, db["app"], "transaction.payment", tid=tid, aid=aid, auid=auid, key="pay-sei"
    )
    assert res.outcome == "completed", res.outcome
    assert res.run.result == {"sei_lodged": True, "activity_id": "ACR-9"}
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    assert [c.run.action_key for c in detail.children] == ["load.account_book", "sei.payment"]


async def test_a_refused_sei_payment_records_the_call_on_the_attempt(db, monkeypatch):
    # A failed provider call must still show its method/url/request/response on the attempt, not a
    # blank "no address recorded". Permanent carries no evidence, so this exercises ctx.evidence.
    tid, aid, auid = await _seed_account(db["app"], custodian="sei")
    engine = _engine(db["app"])

    async def fake(session, *, payload, subjects):
        raise OperationError(
            "SEI could not accept this request (HTTP 500).",
            permanent=True,
            status_code=500,
            method="POST",
            url="/currencyreceiptservices/createcurrencyreceipt",
            request={"external_reference": "g6O1MFt0hE02"},
            response={"detail": "Internal Server Error"},
        )

    monkeypatch.setattr("app.integrations.sei.actions.sei_payment", fake)
    sr, res = await _start_advance(
        engine, db["app"], "transaction.payment", tid=tid, aid=aid, auid=auid, key="pay-sei-500"
    )
    assert res.outcome == "failed"
    assert res.run.state.name == "NeedsAttention"
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    leaf = detail.children[1]
    assert leaf.run.action_key == "sei.payment"
    att = leaf.attempts[0]
    assert att.method == "POST"
    assert att.url == "/currencyreceiptservices/createcurrencyreceipt"
    assert att.status_code == 500
    assert att.request == {"external_reference": "g6O1MFt0hE02"}
    assert att.response == {"detail": "Internal Server Error"}


async def test_a_contribution_runs_through_transaction_payment(db, monkeypatch):
    # The fold: transaction.payment is the one money-in action; a contribution source (no account,
    # a contribution uid) routes to the internal contribution.lodge leaf.
    tid, _aid, _auid = await _seed_account(db["app"])
    engine = _engine(db["app"])

    async def fake(session, *, payload, subjects):
        return Attempt(
            ok=True,
            method="POST",
            url="contribution/lodge",
            response={"status": "instructed", "sei_id": "ACR-2"},
        )

    monkeypatch.setattr("app.actions.money.lodge_contribution", fake)
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sr = await engine.start(
            engine.store.bind(s),
            engine.registry.require("transaction.payment"),
            tenant_id=tid,
            params={"contribution_uid": "C-9"},
            idempotency_key="txn-pay-contrib",
            subjects=[Subject("contribution", "C-9", None)],
        )
    await eng.dispose()
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "completed", res.outcome
    assert res.run.result == {"status": "instructed", "sei_id": "ACR-2"}
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    assert [c.run.action_key for c in detail.children] == ["contribution.lodge"]


async def _seed_ml_sipp(url: str) -> tuple[int, int, str]:
    """A SEI SIPP whose book names Morgan Lloyd as the scheme administrator."""
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    n = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tid = (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES "
                    "('ten_' || :n, 'mny-' || :n, 'Money Firm') RETURNING id"
                ),
                {"n": n},
            )
        ).scalar_one()
        cid = (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname) "
                    "VALUES (:t, :u, 'individual', 'Ada', 'Okonkwo') RETURNING id"
                ),
                {"t": tid, "u": f"con_{n}"},
            )
        ).scalar_one()
        prid = (
            await s.execute(
                text(
                    "INSERT INTO platform.products (product_type, name) "
                    "VALUES (CAST('SIPP' AS platform.product_type), 'SIPP') RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO platform.product_platforms "
                "(tenant_id, product_id, provider, enabled, role) VALUES "
                "(:t, :pr, CAST('morgan-lloyd' AS platform.provider_code), true, "
                "'scheme_administrator')"
            ),
            {"t": tid, "pr": prid},
        )
        aid = (
            await s.execute(
                text(
                    "INSERT INTO platform.accounts "
                    "(tenant_id, uid, contact_id, custodian, product_id, status) VALUES "
                    "(:t, :u, :c, CAST('sei' AS platform.provider_code), :pr, 'active') "
                    "RETURNING id"
                ),
                {"t": tid, "u": f"acc_{n}", "c": cid, "pr": prid},
            )
        ).scalar_one()
    await eng.dispose()
    return int(tid), int(aid), f"acc_{n}"


async def test_transfer_raise_leg_records_the_morgan_lloyd_sipp(db, monkeypatch):
    tid, aid, auid = await _seed_ml_sipp(db["app"])
    engine = _engine(db["app"])

    async def fake_transfer(session, *, payload, subjects):
        return Attempt(ok=True, method="POST", url="/transfers", response={"raised": True})

    async def fake_record(session, *, payload, subjects):
        return Attempt(ok=True, method="POST", url="/dynamics", response={"recorded": True})

    monkeypatch.setattr("app.integrations.sei.actions.sei_transfer", fake_transfer)
    monkeypatch.setattr("app.actions.money.record_transfer", fake_record)
    sr, res = await _start_advance(
        engine, db["app"], "transaction.transfer", tid=tid, aid=aid, auid=auid, key="tr-ml-raise"
    )
    assert res.outcome == "completed", res.outcome
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    assert [c.run.action_key for c in detail.children] == [
        "load.account_book",
        "sei.transfer",
        "morgan-lloyd.record_transfer",
    ]


async def test_transfer_sei_leg_skips_the_morgan_lloyd_record(db, monkeypatch):
    # The later ``:sei`` receipt leg of a SIPP is custodian-only: no second Morgan Lloyd record,
    # even though the book names one. ``leg='sei'`` carries that.
    tid, aid, auid = await _seed_ml_sipp(db["app"])
    engine = _engine(db["app"])

    async def fake_transfer(session, *, payload, subjects):
        return Attempt(ok=True, method="POST", url="/transfers", response={"receipt": "raised"})

    monkeypatch.setattr("app.integrations.sei.actions.sei_transfer", fake_transfer)
    sr, res = await _start_advance(
        engine,
        db["app"],
        "transaction.transfer",
        tid=tid,
        aid=aid,
        auid=auid,
        key="tr-ml-sei",
        leg="sei",
    )
    assert res.outcome == "completed", res.outcome
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    assert [c.run.action_key for c in detail.children] == ["load.account_book", "sei.transfer"]


async def test_transfer_run_records_the_form_as_its_input(db, monkeypatch):
    """The transfer door snapshots every field of the raise form onto the run (`meta.input`), so a
    transfer the custodian refuses still carries what the adviser asked for — ops set it up by
    hand at SEI from the run's Input panel. Provider outcomes on the row are not input."""
    import json

    from app.actions import money

    tid, aid, auid = await _seed_account(db["app"], custodian="sei")
    engine = _engine(db["app"])
    monkeypatch.setattr("app.actions.service.get_engine", lambda: engine)
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    tuid = f"trf_{uuid.uuid4().hex[:8]}"
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        cid = (
            await s.execute(
                text("SELECT contact_id FROM platform.accounts WHERE id = :a"), {"a": aid}
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO platform.transfers (uid, tenant_id, account_id, contact_id, "
                "  ceding_provider, policy_number, transfer_type, basis, extent, crystallisation, "
                "  pension_type, safeguarded_benefits, crystallised_value, uncrystallised_value, "
                "  total_value, currency, expected_date, note, data) "
                "VALUES (:u, :t, :a, :c, 'Aviva', 'AV-123', 'pension', 'in_specie', 'full', "
                "  'uncrystallised', 18, false, 0, 25000, 25000, 'GBP', DATE '2026-11-01', "
                "  'Client confirmed by phone', CAST(:data AS jsonb))"
            ),
            {
                "u": tuid,
                "t": tid,
                "a": aid,
                "c": cid,
                "data": json.dumps(
                    {
                        "provider_address_line1": "1 Pension Way",
                        "provider_postcode": "YO1 1AA",
                        "provider_country": "GB",
                        "fca_reference": "123456",
                        "external_account_type": "SIPP",
                        "documents": [{"document_reference_id": "DOC-9"}],
                        "provider_error": "refused last time",
                        "sei_accepted": False,
                    }
                ),
            },
        )
        snapshot = await money._transfer_input(s, tenant_id=tid, uid=tuid)
    assert snapshot == {
        "client_name": "Ada Okonkwo",
        "account_uid": auid,
        "ceding_provider": "Aviva",
        "policy_number": "AV-123",
        "transfer_type": "pension",
        "basis": "in_specie",
        "extent": "full",
        "crystallisation": "uncrystallised",
        "pension_type": 18,
        "pension_type_label": "Self Invested Personal Pension",
        "safeguarded_benefits": False,
        "crystallised_value": 0.0,
        "uncrystallised_value": 25000.0,
        "total_value": 25000.0,
        "currency": "GBP",
        "expected_date": "2026-11-01",
        "note": "Client confirmed by phone",
        "provider_address_line1": "1 Pension Way",
        "provider_postcode": "YO1 1AA",
        "provider_country": "GB",
        "fca_reference": "123456",
        "external_account_type": "SIPP",
        "documents": [{"document_reference_id": "DOC-9"}],
    }

    # through the door: the run that is created carries the snapshot
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await money.lodge_transfer(
            s,
            tenant_id=tid,
            record={"account_id": aid, "uid": tuid, "contact_id": cid},
            enforce_invoke=False,
        )
        run = await engine.store.run_by_key(engine.store.bind(s), tid, f"transfer:{tuid}")
    await eng.dispose()
    assert run is not None
    assert run.meta["input"]["ceding_provider"] == "Aviva"
    assert run.meta["input"]["documents"] == [{"document_reference_id": "DOC-9"}]
    assert "provider_error" not in run.meta["input"]
