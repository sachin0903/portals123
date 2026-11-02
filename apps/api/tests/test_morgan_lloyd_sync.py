"""`sync_client` — pushing a client and their pension accounts into Morgan Lloyd.

THIS PATH HAD NO TESTS AT ALL, which is notable given that both bugs its docstring records were
in exactly the part no unit test can see from the mapper alone: `create_contact` created the
person every time and then discarded the GUID because it read a flat body where the action
answers an envelope — so each retry duplicated a human being in a real back-office system, and
nothing failed.

So this suite drives the ORCHESTRATION rather than the payloads (`test_morgan_lloyd_contacts.py`
and `test_morgan_lloyd_contact_contract.py` own those): what gets called, in what order, what is
recorded, and — most of all — what a SECOND run does. Morgan Lloyd has no delete.

The session is a fake rather than a real Postgres: what is under test is the decision to call or
skip, and the fake lets a test assert on the exact link rows written without a container. The
links are written under the CLIENT'S OWN tenant (carried on the loaded row, `tenant_id` in the
link params), NOT `auth.tenant_id()`: `client.sync` runs as a baton action under `service_role`
with no request JWT, where that function is NULL — the cause of a links NotNullViolation. The
client's own tenant keeps the old guarantee (you cannot sync a client into another firm).
"""

import logging
from typing import Any

import pytest

from app.integrations.morgan_lloyd import contacts as contacts_mod
from app.integrations.morgan_lloyd import sync
from app.integrations.morgan_lloyd.benefits import MorganLloydNotConfigured

_PRESENT = "configured"

CLIENT: dict[str, Any] = {
    "id": 41,
    "tenant_id": 3,
    "uid": "cli000000001",
    "title": "Mr",
    "first_name": "Tomasz",
    "middle_name": None,
    "surname": "Achebe",
    "email": "tomasz.achebe@example.com",
    "dob": "1971-05-02",
    "nationality": ["GB"],
    "national_identifier": "QQ123456C",
    "gender": "male",
    "mobile": None,
    "phone": "01179000000",
    "marital_status": "married",
    "retirement_age": 65,
    "employment_status": "employed",
    "source_of_wealth": "Salary",
    "relevant_uk_earnings": None,
    "flexi_access_taken": None,
    "is_vulnerable": None,
    "vulnerability_reasons": None,
    "vulnerability_notes": None,
    "address_line1": "4 Queen Square",
    "address_line2": None,
    "address_city": "Bristol",
    "address_county": None,
    "address_postcode": "BS1 4JQ",
    "ml_contact_id": None,
}

SIPP: dict[str, Any] = {
    "id": 900,
    "uid": "acc000000001",
    "name": "Achebe SIPP",
    "value": 250_000.0,
    "product_type": "SIPP",
    "custodian": "sei",
    "product_scheme_type": None,
    "product_sipp_type": None,
    "ml_scheme_ref": None,
    "ml_policy_ref": None,
}

ISA: dict[str, Any] = {**SIPP, "id": 901, "uid": "acc000000002", "product_type": "ISA"}

ADVISER: dict[str, Any] = {
    "user_id": "aaaaaaaa-0000-0000-0000-000000000001",
    "first_name": "Rowan",
    "last_name": "Pryce",
    "email": "rowan.pryce@example.com",
    "ml_adviser_id": None,
}

BENEFICIARY: dict[str, Any] = {
    "id": 17,
    "uid": "ben000000001",
    "account_id": 900,
    "title": "Mrs",
    "name": "Ada Achebe",
    "relationship": "Spouse",
    "proportion": 100,
    "nominated_on": "2020-05-26",
    "address": {"line1": "4 Queen Square", "city": "Bristol", "postcode": "BS1 4JQ"},
    "ml_beneficiary_id": None,
}


class _FakeSession:
    """Answers the module's SELECTs and records every link INSERT / provider_refs UPDATE."""

    def __init__(
        self,
        client: dict[str, Any],
        accounts: list[dict[str, Any]],
        *,
        adviser: dict[str, Any] | None = None,
        beneficiaries: list[dict[str, Any]] | None = None,
    ) -> None:
        self._client = client
        self._accounts = accounts
        self._adviser = adviser
        self._beneficiaries = list(beneficiaries or [])
        self.links: list[dict[str, Any]] = []
        self.provider_refs: list[dict[str, Any]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(statement)
        if "INSERT INTO platform.links" in sql:
            self.links.append(dict(params or {}))
            return _Result([])
        if "UPDATE auth.users" in sql:
            self.provider_refs.append(dict(params or {}))
            return _Result([])
        if "FROM crm.contacts" in sql:
            return _Result([self._client] if self._client else [])
        if "FROM crm.client_advisers" in sql:
            return _Result([self._adviser] if self._adviser else [])
        if "FROM crm.beneficiaries" in sql:
            aid = (params or {}).get("aid")
            rows = self._beneficiaries
            if aid is not None:
                rows = [row for row in rows if row.get("account_id") == aid]
            return _Result(rows)
        if "FROM platform.accounts" in sql:
            return _Result(self._accounts)
        raise AssertionError(f"unexpected statement: {sql[:120]}")


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _Settings:
    tenant_id = client_id = client_secret = _PRESENT
    adviser_firm_id = "6ac39921-d8b7-f011-bbd3-7ced8d6b85ff"
    adviser_contact_id = "4dc533b3-0000-0000-0000-000000000000"
    scheme_type: int | None = 16
    sipp_type: int | None = 809130016
    investment_provider_id = ""

    @property
    def investment_provider(self) -> str:
        return self.investment_provider_id or self.adviser_firm_id


class _FakeApi:
    """Stands in for MorganLloydClient, recording every call and answering per-action."""

    def __init__(self, **answers: Any) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> "_FakeApi":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def _answer(self, action: str, body: dict[str, Any]) -> Any:
        self.calls.append((action, body))
        answer = self.answers.get(action, {})
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def create_contact(self, body: dict[str, Any]) -> Any:
        return self._answer("create_contact", body)

    async def create_scheme(self, body: dict[str, Any]) -> Any:
        return self._answer("create_scheme", body)

    async def create_investment(self, body: dict[str, Any]) -> Any:
        return self._answer("create_investment", body)

    async def create_financial_adviser_contact(self, body: dict[str, Any]) -> Any:
        return self._answer("create_financial_adviser_contact", body)

    async def create_beneficiary(self, body: dict[str, Any]) -> Any:
        return self._answer("create_beneficiary", body)

    async def get_financial_adviser_contacts(self, firm_id: str) -> Any:
        self.calls.append(("get_financial_adviser_contacts", {"firm_id": firm_id}))
        answer = self.answers.get("get_financial_adviser_contacts", [])
        if isinstance(answer, Exception):
            raise answer
        return answer

    def called(self, action: str) -> list[dict[str, Any]]:
        return [body for name, body in self.calls if name == action]


#: The vendor's answers for a clean run.
_VENDOR_ANSWERS: dict[str, Any] = {
    "create_contact": {"message": "ok", "response": "9f2c1c76-1111-2222-3333-444455556666"},
    "create_scheme": {"schemeReference": "MLSIPP/02039"},
    "create_investment": {"wrapper": SIPP["uid"]},
}


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> _FakeApi:
    """A configured vendor client that succeeds at all three calls."""
    stub = _FakeApi(**_VENDOR_ANSWERS)
    _install(monkeypatch, stub)
    return stub


def _install(monkeypatch: pytest.MonkeyPatch, stub: _FakeApi, **settings: Any) -> None:
    resolved = _Settings()
    for key, value in settings.items():
        setattr(resolved, key, value)
    for module in (sync, contacts_mod):
        monkeypatch.setattr(module, "configured", lambda: True)
        monkeypatch.setattr(module, "_settings", lambda resolved=resolved: resolved)
    import morgan_lloyd.client as vendor

    monkeypatch.setattr(vendor, "MorganLloydClient", lambda settings=None: stub)


def _session(
    client: dict[str, Any] | None = None,
    accounts: list[dict[str, Any]] | None = None,
    *,
    adviser: dict[str, Any] | None = None,
    beneficiaries: list[dict[str, Any]] | None = None,
) -> _FakeSession:
    return _FakeSession(
        dict(client or CLIENT),
        [dict(a) for a in (accounts or [SIPP])],
        adviser=adviser,
        beneficiaries=beneficiaries,
    )


def _link(session: _FakeSession, kind: str, link_type: str) -> dict[str, Any] | None:
    return next(
        (row for row in session.links if row["kind"] == kind and row.get("link_type") == link_type),
        None,
    )


class TestTheFullChain:
    async def test_creates_contact_then_scheme_then_policy(self, api: _FakeApi) -> None:
        """The order is a dependency, not a preference: a scheme is created against a contact
        GUID and a policy against a scheme reference."""
        session = _session()
        report = await sync.sync_client(session, contact_id=41)

        assert [name for name, _ in api.calls] == [
            "create_contact",
            "create_scheme",
            "create_investment",
        ]
        assert report["contact"]["status"] == "created"
        assert report["accounts"][0]["status"] == "created"
        assert report["accounts"][0]["policy_status"] == "created"

    async def test_records_a_link_for_each_of_the_three(self, api: _FakeApi) -> None:
        """The links ARE the idempotence. A created record with no link is the worst of the three
        outcomes — it duplicates on the next run."""
        session = _session()
        await sync.sync_client(session, contact_id=41)

        assert _link(session, "contact", "primary") == {
            "tenant_id": 3,
            "kind": "contact",
            "eid": 41,
            "ext": "9f2c1c76-1111-2222-3333-444455556666",
            "link_type": "primary",
        }
        assert _link(session, "account", "primary")["ext"] == "MLSIPP/02039"
        assert _link(session, "account", "policy")["ext"] == SIPP["uid"]

    async def test_the_scheme_and_policy_links_are_distinct_types(self, api: _FakeApi) -> None:
        """One account, two identifiers. Collapsing them onto one `link_type` would make the
        second write overwrite the first, and valuations would then be filed against a scheme
        reference the vendor refuses."""
        session = _session()
        await sync.sync_client(session, contact_id=41)

        account_links = [row for row in session.links if row["kind"] == "account"]
        assert {row["link_type"] for row in account_links} == {"primary", "policy"}
        assert len({row["ext"] for row in account_links}) == 2

    async def test_the_scheme_is_created_against_the_new_contact_guid(self, api: _FakeApi) -> None:
        session = _session()
        await sync.sync_client(session, contact_id=41)
        assert api.called("create_scheme")[0]["contactId"] == (
            "9f2c1c76-1111-2222-3333-444455556666"
        )

    async def test_the_policy_is_created_against_the_new_scheme_reference(
        self, api: _FakeApi
    ) -> None:
        """Not the scheme's GUID and not the account uid — the `MLSIPP/…` reference."""
        session = _session()
        await sync.sync_client(session, contact_id=41)
        assert api.called("create_investment")[0]["schemeReference"] == "MLSIPP/02039"


class TestIdempotence:
    """Morgan Lloyd has no delete, so a second run creating a second anything is unrecoverable."""

    async def test_an_already_linked_client_and_account_calls_nothing(self, api: _FakeApi) -> None:
        session = _session(
            {**CLIENT, "ml_contact_id": "existing-guid"},
            [{**SIPP, "ml_scheme_ref": "MLSIPP/00007", "ml_policy_ref": "acc000000001"}],
        )
        report = await sync.sync_client(session, contact_id=41)

        assert api.calls == []
        assert session.links == []
        assert report["contact"] == {"status": "skipped", "external_id": "existing-guid"}
        assert report["accounts"][0]["status"] == "skipped"
        assert report["accounts"][0]["policy_status"] == "skipped"

    async def test_a_scheme_without_a_policy_resumes_at_the_policy(self, api: _FakeApi) -> None:
        """The half-done state this change introduces, and the one a retry has to handle: the
        scheme landed and the policy call failed. A second run must not create a second scheme."""
        session = _session(
            {**CLIENT, "ml_contact_id": "existing-guid"},
            [{**SIPP, "ml_scheme_ref": "MLSIPP/00007", "ml_policy_ref": None}],
        )
        report = await sync.sync_client(session, contact_id=41)

        assert api.called("create_scheme") == []
        assert len(api.called("create_investment")) == 1
        assert api.called("create_investment")[0]["schemeReference"] == "MLSIPP/00007"
        assert report["accounts"][0]["status"] == "skipped"
        assert report["accounts"][0]["policy_status"] == "created"
        assert [row["link_type"] for row in session.links] == ["policy"]

    async def test_a_linked_contact_with_an_unlinked_account_creates_only_the_scheme_side(
        self, api: _FakeApi
    ) -> None:
        session = _session({**CLIENT, "ml_contact_id": "existing-guid"}, [SIPP])
        await sync.sync_client(session, contact_id=41)
        assert api.called("create_contact") == []
        assert len(api.called("create_scheme")) == 1


class TestWhatIsNotAPension:
    async def test_a_non_pension_account_gets_neither_a_scheme_nor_a_policy(
        self, api: _FakeApi
    ) -> None:
        """A GIA or an ISA has no scheme in Morgan Lloyd's model, so there is nothing to create
        and nothing to report against it."""
        session = _session(accounts=[ISA])
        report = await sync.sync_client(session, contact_id=41)

        assert api.called("create_scheme") == []
        assert api.called("create_investment") == []
        assert report["accounts"] == []
        assert any("no pension account" in w for w in report["warnings"])

    async def test_a_seccl_sipp_is_refused_by_name(self, api: _FakeApi) -> None:
        """Asked for a Seccl SIPP, the refusal says Morgan Lloyd administers SEI SIPPs. Seccl
        administers that scheme itself."""
        session = _session(accounts=[{**SIPP, "custodian": "seccl"}])

        with pytest.raises(ValueError, match="SEI SIPPs"):
            await sync.sync_client(session, contact_id=41, account_id=900)
        assert api.calls == []

    async def test_a_whole_client_push_skips_a_seccl_sipp(self, api: _FakeApi) -> None:
        seccl = {**SIPP, "id": 903, "uid": "acc000000004", "custodian": "seccl"}
        session = _session(accounts=[SIPP, seccl])
        report = await sync.sync_client(session, contact_id=41)

        assert [step["account_id"] for step in report["accounts"]] == [SIPP["id"]]

    async def test_a_mixed_book_pushes_only_the_pension(self, api: _FakeApi) -> None:
        session = _session(accounts=[ISA, SIPP])
        report = await sync.sync_client(session, contact_id=41)

        assert len(report["accounts"]) == 1
        assert report["accounts"][0]["account_id"] == SIPP["id"]

    @pytest.mark.parametrize("product", ["SIPP", "PENSION", "SSAS", "sipp"])
    async def test_every_pension_product_type_is_recognised(
        self, api: _FakeApi, product: str
    ) -> None:
        """Case-insensitively — `product_type` is an enum read as text."""
        session = _session(accounts=[{**SIPP, "product_type": product}])
        report = await sync.sync_client(session, contact_id=41)
        assert len(report["accounts"]) == 1


class TestHardPreconditions:
    async def test_no_credentials_refuses_outright(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sync, "configured", lambda: False)
        with pytest.raises(MorganLloydNotConfigured):
            await sync.sync_client(_session(), contact_id=41)

    async def test_an_unknown_client_is_a_lookup_error_even_when_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing person is 404, not 503. CI has no MORGAN_LLOYD_* so checking credentials
        first made every unknown id look like a deployment problem."""
        monkeypatch.setattr(sync, "configured", lambda: False)
        session = _FakeSession({}, [])
        with pytest.raises(LookupError, match="Client not found"):
            await sync.sync_client(session, contact_id=41)

    async def test_an_unknown_client_is_a_lookup_error(self, api: _FakeApi) -> None:
        session = _FakeSession({}, [])
        with pytest.raises(LookupError):
            await sync.sync_client(session, contact_id=41)

    async def test_missing_required_details_refuse_before_any_call(self, api: _FakeApi) -> None:
        """A 422 naming what an adviser has to fill in, rather than the vendor's
        `0x80040224 Object reference not set to an instance of an object`."""
        session = _session({**CLIENT, "gender": None, "national_identifier": None})
        with pytest.raises(ValueError) as exc:
            await sync.sync_client(session, contact_id=41)

        assert "gender" in str(exc.value)
        assert "National Insurance" in str(exc.value)
        assert api.calls == []

    async def test_an_unconfigured_adviser_firm_warns_rather_than_refusing(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Without it the contact is created unattached and every later scheme 403s — said now,
        while the person reading it can still act, rather than as a permissions error later."""
        stub = _FakeApi(
            create_contact={"response": "9f2c1c76-1111-2222-3333-444455556666"},
            create_scheme={"schemeReference": "MLSIPP/02039"},
            create_investment={"wrapper": "acc000000001"},
        )
        _install(monkeypatch, stub, adviser_firm_id="", investment_provider_id="ok-provider")
        session = _session()
        with caplog.at_level(logging.WARNING):
            report = await sync.sync_client(session, contact_id=41)

        # The WARNING says the adviser firm is missing; the setting behind it is logged, since the
        # person reading a sync report cannot set one.
        assert any("adviser firm is missing" in w for w in report["warnings"])
        assert "MORGAN_LLOYD_ADVISER_FIRM_ID" in caplog.text
        assert report["contact"]["status"] == "created"


class TestTheAccountProjectionFeedsTheMapper:
    """`build_contact` reads a dict with `.get`, so an unselected column is indistinguishable
    from a client who has not answered. These assert the shared projection is actually used."""

    def test_the_client_projection_selects_everything_the_mapper_reads(self) -> None:
        for column in (
            "uid",
            "employment_status",
            "relevant_uk_earnings",
            "vulnerability_reasons",
            "vulnerability_notes",
            "source_of_wealth",
            "source_of_wealth_detail",
            "flexi_access_taken",
            "gender",
        ):
            assert f"c.{column}" in sync._CLIENT_SQL, f"{column} is not selected"

    def test_the_client_projection_selects_both_extra_address_parts(self) -> None:
        assert "address_line2" in sync._CLIENT_SQL
        assert "address_county" in sync._CLIENT_SQL

    def test_the_account_projection_carries_the_uid_the_policy_number_is_taken_from(self) -> None:
        assert "a.uid" in sync._ACCOUNTS_SQL

    def test_the_account_projection_reads_both_link_types(self) -> None:
        assert "'primary'" in sync._ACCOUNTS_SQL
        assert "'policy'" in sync._ACCOUNTS_SQL

    def test_the_account_projection_reads_the_products_own_scheme_types(self) -> None:
        assert "ml_scheme_type" in sync._ACCOUNTS_SQL
        assert "ml_sipp_type" in sync._ACCOUNTS_SQL
        assert "platform.product_platforms" in sync._ACCOUNTS_SQL

    def test_the_adviser_projection_reads_provider_refs(self) -> None:
        """`platform.links.entity_id` is bigint and a login is a uuid, so the mapping is a
        column on `auth.users` (0131), not a link row."""
        assert "provider_refs" in sync._ADVISER_SQL
        assert "role = 'adviser'" in sync._ADVISER_SQL

    def test_the_adviser_guid_is_cast_before_it_enters_jsonb(self) -> None:
        """`jsonb_build_object` is VARIADIC any. An untyped `:ext` is a 500 after the vendor
        has already created the adviser — `IndeterminateDatatype` on parameter $1. The fake
        session never sends this statement to Postgres, so the CAST has to be pinned here."""
        assert "CAST(:ext AS text)" in sync._PROVIDER_REFS_SQL

    def test_nominations_are_linked_as_beneficiary_rows(self) -> None:
        assert "entity_type = 'beneficiary'" in sync._BENEFICIARIES_SQL
        assert "crm.beneficiaries" in sync._BENEFICIARIES_SQL


# --------------------------------------------------------------------------------------------
# The one claim a fake session cannot make.
# --------------------------------------------------------------------------------------------
#
# Everything above asserts on the DECISION to call or skip, with the SQL asserted as text. That
# leaves the query itself unproven, and its failure mode is silent in exactly the way this area
# keeps producing: `platform.product_platforms` is RLS'd `tenant_id = ANY (auth.tenant_lineage())`
# and `platform.platforms` is a global registry that `providers.sync_platforms()` seeds at
# startup — so if the join were wrong, or the row unreadable through a request session, both
# product columns would come back NULL, `scheme_types` would fall back to the deployment pairing,
# and every test above would still pass while the product's configuration never applied.
#
# `platform.platforms` had genuinely never been written to for months (CLAUDE.md records it
# costing every account its `product_id`), which is what makes the seeded-registry half worth
# executing rather than reasoning about.


@pytest.mark.asyncio
async def test_the_account_projection_really_reads_the_products_pairing(
    service_session: Any,
) -> None:
    """Seed a firm, a product and its Morgan Lloyd pairing, then EXECUTE the real `_ACCOUNTS_SQL`
    and assert the pairing comes back.

    This proves the JOIN and the jsonb read — the two things the fake-session tests above cannot
    see. It runs as `service_role`, so it deliberately does not claim anything about RLS
    visibility; that is asserted separately below, off the policy text, the posture
    `test_aml_rls.py` takes for a rule whose failure is a silently empty read.
    """
    import uuid

    from sqlalchemy import text

    from tests.conftest import SECCL_PLATFORM_SQL

    suffix = uuid.uuid4().hex[:8]
    tenant_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"mls_{suffix}", "a": f"mls-{suffix}", "n": f"ML sync {suffix}"},
            )
        ).scalar_one()
    )
    # THE PROVIDER CODE, and there is no row to mint (0131). This used to insert the ML
    # `platform.platforms` row "the way sync_platforms writes it", shared across tests because
    # 0055 made `provider` unique — a whole coordination problem that existed because a member of
    # a closed vocabulary had been given a surrogate id.
    ml_platform = "morgan-lloyd"
    await service_session.execute(text(SECCL_PLATFORM_SQL))

    product_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO platform.products (tenant_id, name, code, product_type) "
                    "VALUES (:t, :n, 'SIPP', 'SIPP') RETURNING id"
                ),
                {"t": tenant_id, "n": f"SIPP {suffix}"},
            )
        ).scalar_one()
    )
    await service_session.execute(
        text(
            "INSERT INTO platform.product_platforms "
            "  (tenant_id, product_id, provider, external_id, data) "
            "VALUES (:t, :pr, :pl, :ext, cast(:data AS jsonb))"
        ),
        {
            "t": tenant_id,
            "pr": product_id,
            "pl": ml_platform,
            "ext": f"ML-SIPP-{suffix}",
            "data": '{"ml_scheme_type": 17, "ml_sipp_type": 809130002}',
        },
    )
    contact_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO crm.contacts "
                    "  (tenant_id, uid, type, name, first_name, surname, email) "
                    "VALUES (:t, :u, 'individual', :n, 'Ada', 'Nwosu', :e) RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "u": f"c{suffix}0000"[:12],
                    "n": "Ada Nwosu",
                    "e": f"ada.{suffix}@example.com",
                },
            )
        ).scalar_one()
    )
    await service_session.execute(
        text(
            "INSERT INTO platform.accounts "
            "  (tenant_id, uid, contact_id, product_id, name, status) "
            "VALUES (:t, :u, :c, :p, 'Nwosu SIPP', 'active')"
        ),
        {"t": tenant_id, "u": f"a{suffix}0000"[:12], "c": contact_id, "p": product_id},
    )

    rows = (
        (
            await service_session.execute(
                text(sync._ACCOUNTS_SQL),
                {"cid": contact_id, "custodians": ["seccl", "sei"]},
            )
        )
        .mappings()
        .all()
    )
    assert len(rows) == 1, "the account projection did not return the seeded account"
    row = dict(rows[0])

    # The whole point: the PRODUCT's pairing, read through the real join.
    assert row["product_scheme_type"] == "17"
    assert row["product_sipp_type"] == "809130002"
    assert sync.scheme_types(row) == (17, 809130002)

    # And the two link columns are distinguishable and both empty on a fresh account — a join
    # that matched the wrong `link_type` would show one under the other.
    assert row["ml_scheme_ref"] is None
    assert row["ml_policy_ref"] is None
    assert row["uid"], "the policy number is taken from this"


@pytest.mark.asyncio
async def test_recording_an_adviser_guid_does_not_need_postgres_to_guess_the_type(
    service_session: Any,
) -> None:
    """Execute the real `_PROVIDER_REFS_SQL`. The fake session records the params and never
    asks Postgres, so `jsonb_build_object('morgan-lloyd', :ext)` passed every unit test and
    500'd live after `cam_CreateFinancialAdviserContact` had already minted the person."""
    import json
    import uuid

    from sqlalchemy import text

    suffix = uuid.uuid4().hex[:8]
    tenant_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"mlu_{suffix}", "a": f"mlu-{suffix}", "n": f"ML user {suffix}"},
            )
        ).scalar_one()
    )
    user_id = str(
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.users (tenant_id, email, first_name, enabled, type) "
                    "VALUES (:t, :e, 'Rowan', true, ARRAY['staff']::text[]) RETURNING id"
                ),
                {"t": tenant_id, "e": f"rowan.{suffix}@example.com"},
            )
        ).scalar_one()
    )
    await service_session.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"tenant_id": tenant_id})},
    )
    guid = "a964abd4-6db3-f111-aaac-6045bd0e08b2"
    await service_session.execute(
        text(sync._PROVIDER_REFS_SQL), {"u": user_id, "ext": guid, "tenant_id": tenant_id}
    )
    stored = (
        await service_session.execute(
            text("SELECT provider_refs FROM auth.users WHERE id = CAST(:u AS uuid)"),
            {"u": user_id},
        )
    ).scalar_one()
    assert stored == {"morgan-lloyd": guid}


@pytest.mark.asyncio
async def test_both_joined_tables_are_readable_by_a_request_principal(
    service_session: Any,
) -> None:
    """The RLS half, asserted off the catalogue rather than behaviourally.

    If either table were unreadable through a request session the join would yield NULL for both
    product columns, `scheme_types` would fall back to the deployment pairing, and every other
    test in this file would still pass — the product's configuration would simply never apply.
    That is the failure this area keeps producing, so the two policies are pinned:

    ONE TABLE NOW (0131). It used to be two: `platform.platforms`, a GLOBAL registry
    (`USING (true)`) holding the provider list, and the mapping that FK'd into it. The registry
    was a surrogate id for a member of a closed vocabulary, so it is `platform.provider_code` and
    there is no second policy to get wrong — which also removes the failure this docstring was
    written about, since a provider value cannot be unreadable.

    What remains pinned is the half that carries tenant data: `platform.product_platforms` is
    scoped by `auth.tenant_lineage()`, which is what makes a firm's own pairing visible to its own
    staff and to nobody else. It returns `'{}'` for a client-portal principal, so a client reaches
    none of it.
    """
    from sqlalchemy import text

    policies = {
        row["tablename"]: (row["qual"] or "")
        for row in (
            await service_session.execute(
                text(
                    "SELECT tablename, qual FROM pg_policies "
                    "WHERE schemaname = 'platform' "
                    "  AND tablename = 'product_platforms' "
                    "  AND cmd = 'SELECT' AND 'authenticated' = ANY (roles)"
                )
            )
        )
        .mappings()
        .all()
    }
    assert set(policies) == {"product_platforms"}, (
        f"a SELECT policy for authenticated is missing: {sorted(policies)}"
    )
    assert "tenant_lineage" in policies["product_platforms"]

    # `has_table_privilege`, not `information_schema.role_table_grants` — that view is filtered
    # to grants the CURRENT role is party to, so asking it as service_role about `authenticated`
    # returns nothing and the assertion would have been vacuously wrong in the other direction.
    granted = (
        (
            await service_session.execute(
                text(
                    "SELECT has_table_privilege("
                    "    'authenticated', 'platform.product_platforms', 'SELECT')"
                    "  AS product_platforms"
                )
            )
        )
        .mappings()
        .one()
    )
    assert granted["product_platforms"], (
        "a policy without the grant is an unreadable table with a policy that looks right"
    )
