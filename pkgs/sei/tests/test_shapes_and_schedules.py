"""The two pieces of this client that contain logic rather than a path.

`sei.endpoints._shapes` matters because SEI wraps a payload four different ways across its
services and a wrong guess returns an EMPTY collection rather than raising — which reads as
"this firm has no data" and is the failure mode `docs/sei.md` records hitting repeatedly.

`sei.models.Recurring` matters because a standing order sent with the wrong day resolution
moves a contribution into the next month, and therefore the next tax year.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest

from sei import SeiApiError
from sei.endpoints._shapes import attributes, rows
from sei.endpoints.reference import ReferenceApi, system_code_rows
from sei.enums import DayType, ResolveOption
from sei.models import Recurring


class TestAttributes:
    def test_unwraps_json_api(self) -> None:
        assert attributes({"data": {"type": "X", "attributes": {"status": "SUCCESS"}}}) == {
            "status": "SUCCESS"
        }

    def test_unwraps_the_first_entry_of_a_data_array(self) -> None:
        body = {"data": [{"attributes": {"activityId": "1"}}, {"attributes": {"activityId": "2"}}]}
        assert attributes(body) == {"activityId": "1"}

    def test_unwraps_the_soap_derived_double_envelope(self) -> None:
        """The cash services name both layers after the operation, so `status` is two levels
        down and a caller reading `body["status"]` finds nothing."""
        body = {
            "data": {
                "createCashDisbursementResponse": {
                    "CreateCurrencyDisbursementDetails": {
                        "status": "SUCCESS",
                        "currencyDisbursementId": "1019910419",
                    }
                }
            }
        }
        assert attributes(body)["currencyDisbursementId"] == "1019910419"

    def test_a_bare_object_is_returned_unchanged(self) -> None:
        """`GET /activitymanagementservices/activity/{id}` answers with no envelope at all."""
        body = {"activityId": 1017855289, "activityState": 22, "activityStatus": 7}
        assert attributes(body) == body

    def test_an_unrecognised_body_degrades_rather_than_raising(self) -> None:
        assert attributes(None) == {}
        assert attributes("nope") == {}


class TestRows:
    def test_a_bare_array(self) -> None:
        assert rows([{"a": 1}, {"a": 2}]) == [{"a": 1}, {"a": 2}]

    def test_a_data_array_of_json_api_entries(self) -> None:
        body = {"data": [{"attributes": {"id": 1}}, {"attributes": {"id": 2}}]}
        assert rows(body) == [{"id": 1}, {"id": 2}]

    def test_a_named_collection_inside_data(self) -> None:
        """The retrieval services publish a collection under its own name, so the reader has
        to find the list rather than know what it is called."""
        body = {"data": {"firmId": 3102, "listOfTransactionDetails": [{"transactionId": 1}]}}
        assert rows(body) == [{"transactionId": 1}]

    def test_an_object_with_no_collection_becomes_one_row(self) -> None:
        assert rows({"data": {"attributes": {"id": 7}}}) == [{"id": 7}]

    def test_an_empty_named_collection_is_not_mistaken_for_the_row_itself(self) -> None:
        """An empty list is a real answer ("this firm has none") and must not fall through to
        returning the envelope as a single row — which would read as one result."""
        assert rows({"data": {"firmId": 3102, "templates": []}}) == [
            {"firmId": 3102, "templates": []}
        ]


class TestRecurring:
    def test_monthly_on_resolves_backwards_by_default(self) -> None:
        """A contribution dated the 31st that resolves FORWARD lands in the following month
        and is counted against the wrong allowance."""
        schedule = Recurring.monthly_on(date(2026, 9, 1), day_of_month=31)
        assert schedule.monthly is not None
        assert schedule.monthly.resolve_option == ResolveOption.PRIOR
        assert schedule.monthly.month_day_num == "31"
        assert schedule.monthly.day_type == DayType.CALENDAR

    def test_business_days_are_opt_in(self) -> None:
        schedule = Recurring.monthly_on(date(2026, 9, 1), day_of_month=6, business_days=True)
        assert schedule.monthly is not None
        assert schedule.monthly.day_type == DayType.BUSINESS

    def test_quarterly_is_monthly_with_an_interval(self) -> None:
        """SEI's `RECURRENCE_FREQUENCY` has exactly three members (Daily/Weekly/Monthly) —
        there is no quarterly, so a quarter has to be expressed this way."""
        schedule = Recurring.monthly_on(date(2026, 4, 6), day_of_month=6, interval=3)
        assert schedule.monthly is not None
        assert schedule.monthly.interval == "3"

    def test_every_value_serialises_as_a_string(self) -> None:
        """SEI types the whole payload `string`; a JSON number is rejected."""
        payload = Recurring.monthly_on(date(2026, 9, 1), day_of_month=6).model_dump(
            by_alias=True, exclude_none=True
        )
        assert payload == {
            "startDate": "2026-09-01",
            "monthly": {
                "interval": "1",
                "dayType": "1",
                "monthDayNum": "6",
                "resolveOption": "1",
            },
        }


class _StubReference(ReferenceApi):
    """A ReferenceApi with no transport: the endpoint's own envelope handling is what is under test.

    `__init__` is overridden to skip the base client's settings and TLS setup — this asserts on how
    a response is read, not on how a call is made (`test_endpoint_paths.py` already checks the path
    against SEI's own spec).
    """

    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"method": method, "path": path, "params": params})
        return self.body


class TestFirmEmployeeUsers:
    """SEI names this collection after the request rather than using its generic envelope.

    Which is the trap `_shapes` exists for: `rows()` looks for `data`, finds nothing, and returns
    an EMPTY list — the failure `docs/sei.md` records hitting repeatedly, because "this firm has no
    employees" and "we unwrapped the wrong key" are the same value at the caller.

    This is the option list for mapping a Graphene login to the SEI employee it is. An empty one
    would leave an operator unable to configure the mapping, with no reason shown.
    """

    #: One employee, as `firmemployeeusers` returns them — with the structured name and the EMAIL
    #: that the nightly `UserDetail` report carries neither of.
    EMPLOYEE = {
        "uniqueUserId": 57817496,
        "emailId": "maria.armasoiu@example.test",
        "firstName": "Maria",
        "lastName": "Armasoiu",
    }

    #: The paging block SEI sends alongside the collection — itself a list of one object, which is
    #: what makes the generic unwrapper ambiguous here.
    META = [{"pageNumber": 1, "pageSize": 100, "totalRecords": 428}]

    def test_the_generic_unwrapper_refuses_rather_than_picking(self) -> None:
        """Why this endpoint names its key — and what `rows()` does now when nobody does.

        It used to fall back to "the first list-of-objects in the body", so with two collections
        the answer depended on JSON key order and on whether the real one happened to be all
        dicts: a picker offering "pageNumber 1, pageSize 100, totalRecords 428" as a person, which
        is worse than an empty list because an empty list reads as "not configured" and this reads
        as an answer. This test PINNED that wrong answer; it now pins the refusal, which is what
        turns the next two-collection response into a failure instead of a wrong row.
        """
        body = {"metaData": self.META, "employeeUsersForFirmList": [self.EMPLOYEE]}
        with pytest.raises(SeiApiError, match="ambiguous response shape"):
            rows(body)

        # …and the declared key is the way through, whatever the order.
        assert rows(body, key="employeeUsersForFirmList") == [self.EMPLOYEE]

    def test_a_declared_key_that_is_absent_or_empty_is_an_empty_answer(self) -> None:
        """The case the four keyed endpoints exist for: an EMPTY real collection must not fall
        through to the warning or exception block sitting beside it."""
        beside_a_warning = {"holdingsDetails": [], "warning": [{"code": "1"}]}
        assert rows(beside_a_warning, key="holdingsDetails") == []
        assert rows({"warning": [{"code": "1"}]}, key="holdingsDetails") == []

    def test_the_endpoint_reads_its_own_envelope(self) -> None:
        stub = _StubReference(
            {
                "employeeUsersForFirmList": [self.EMPLOYEE, "not a dict"],
                "metaData": self.META,
            }
        )
        found = asyncio.run(stub.firm_employee_users(processing_org_id=3100, service_id=1))

        # The email is what makes an exact-match proposal defensible, where a display-name match
        # would be wrong 84% of the time (only 37 of 233 DSR employee ids are SEI logins at all).
        assert found == [self.EMPLOYEE]
        assert stub.calls[0]["method"] == "GET"
        # Scoped by the PROCESSING ORG (not the firm id) plus a service filter. 3100 is
        # Blackfinch 3102's processing org; service 1 is the widest populated filter on i31.
        assert stub.calls[0]["params"] == {
            "processingOrgId": "3100",
            "serviceId": "1",
            "pageNumber": "1",
            "pageSize": "100",
        }

    def test_a_non_dict_entry_is_dropped_rather_than_crashing_the_picker(self) -> None:
        stub = _StubReference({"employeeUsersForFirmList": [self.EMPLOYEE, "not a dict", None]})
        found = asyncio.run(stub.firm_employee_users(processing_org_id=3106))
        assert found == [self.EMPLOYEE]

    def test_an_over_large_page_size_is_clamped_to_what_sei_accepts(self) -> None:
        stub = _StubReference({})
        asyncio.run(stub.firm_employee_users(processing_org_id=3106, page_size=5000))
        assert stub.calls[0]["params"]["pageSize"] == "100"

    def test_a_zero_page_size_is_raised_to_one(self) -> None:
        stub = _StubReference({})
        asyncio.run(stub.firm_employee_users(processing_org_id=3106, page_size=0))
        assert stub.calls[0]["params"]["pageSize"] == "1"

    def test_an_empty_body_is_an_empty_list_not_an_error(self) -> None:
        assert asyncio.run(_StubReference({}).firm_employee_users()) == []

    def test_an_absent_scope_is_omitted_from_the_query_not_sent_blank(self) -> None:
        """The client omits absent scopes because a blank is a value SEI would match on.

        Live i31 nevertheless 400s without both (`processingOrgId` code 100014, then
        `serviceId` 100015). Unscoped is not a legitimate sweep — the pipeline must resolve
        the processing org from firm defaults and send a service id. This assertion is only
        that THIS client does not send empty strings.
        """
        stub = _StubReference({"employeeUsersForFirmList": [self.EMPLOYEE]})
        asyncio.run(stub.firm_employee_users())
        params = stub.calls[0]["params"]
        assert "processingOrgId" not in params
        assert "serviceId" not in params
        assert params["pageNumber"] == "1"

        stub = _StubReference({"employeeUsersForFirmList": [self.EMPLOYEE]})
        asyncio.run(stub.firm_employee_users(processing_org_id=3102))
        assert stub.calls[0]["params"]["processingOrgId"] == "3102"
        assert "serviceId" not in stub.calls[0]["params"]


class TestSystemCodeRows:
    def test_reads_the_cache_list_the_spec_example_uses(self) -> None:
        body = {
            "data": {
                "result": {
                    "SYSTEM_CODES_CACHE": [
                        {"codeValue": 2, "shortDescription": "NonTaxable"},
                    ]
                },
                "status": "SUCCESS",
            }
        }
        assert system_code_rows(body) == [{"codeValue": 2, "shortDescription": "NonTaxable"}]

    def test_an_empty_cache_string_is_an_empty_list(self) -> None:
        """i31 answers SUCCESS with ``SYSTEM_CODES_CACHE: ""`` when the set is not loaded.

        ``rows()`` cannot see past ``result`` and would return ``{result, status}`` as the one
        code, which then fails any test looking for a description.
        """
        body = {"data": {"result": {"SYSTEM_CODES_CACHE": ""}, "status": "SUCCESS"}}
        assert system_code_rows(body) == []
