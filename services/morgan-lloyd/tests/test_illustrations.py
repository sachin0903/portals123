"""Illustration builders: CTC1 XML schema fidelity + local PDF rendering."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_illustrations as gi  # noqa: E402


def _inputs() -> gi.IllustrationInputs:
    return gi.synthetic_inputs(1, "2026-07-14")[0]


def test_xml_message_structure_matches_ctc_schema() -> None:
    root = ET.fromstring(gi.build_illustration_xml(_inputs()))
    assert root.tag == "message"
    assert root.findtext("header/message_version") == "CTC1"
    assert root.findtext("header/mode") == "Request"
    quotations = root.find("content/quotations")
    assert quotations is not None
    assert quotations.findtext("effective_date") == "2026-07-14"
    assert quotations.findtext("output_required") == "Yes"
    assert quotations.findtext("output_reference") == "GRAPHENE-TEST-0000"
    assert quotations.findtext("client_type") == "Individual"


def test_xml_investor_and_quotation_mandatory_fields() -> None:
    root = ET.fromstring(gi.build_illustration_xml(_inputs()))
    investor = root.find("content/quotations/investor")
    assert investor is not None
    assert investor.findtext("sex") in ("Male", "Female")
    assert investor.findtext("life_type") == "Applicant"
    quotation = root.find("content/quotations/quotation")
    assert quotation is not None
    assert quotation.get("product_ref") == "SIPPEN"
    assert quotation.get("product_type") == "Self Invested Personal Pension"
    assert quotation.findtext("quotation_type") == "Drawdown"


def test_xml_plan_stream_and_withdrawals_rules() -> None:
    root = ET.fromstring(gi.build_illustration_xml(_inputs()))
    stream = root.find("content/quotations/quotation/plan_stream")
    assert stream is not None
    # schema: Existing Fund Value must be Single-frequency; crystallised Yes/No
    assert stream.findtext("contribution_type") == "Existing Fund Value"
    assert stream.findtext("frequency") == "Single"
    assert stream.findtext("crystallised") in ("Yes", "No")
    withdrawals = root.find("content/quotations/quotation/withdrawals")
    assert withdrawals is not None
    assert withdrawals.findtext("basis") == "Fixed"
    assert withdrawals.findtext("amount") is not None  # only allowed when basis=Fixed
    assert withdrawals.findtext("drawdown_type") in ("Flexible", "Capped", "UFPLS")


def test_xml_premium_split_totals_100_and_fee_mandatory_fields() -> None:
    root = ET.fromstring(gi.build_illustration_xml(_inputs()))
    splits = root.findall("content/quotations/quotation/premium_split/fund_split")
    assert splits and sum(int(s.findtext("split_per") or 0) for s in splits) == 100
    # generic fund carries rate_of_return/management_charge, so must say so
    assert all(s.findtext("generic_fund") == "Yes" for s in splits)
    fee = root.find("content/quotations/quotation/adviser_fees/adviser_fee")
    assert fee is not None
    assert fee.findtext("fee_basis") in ("Initial", "Ongoing")
    assert fee.findtext("fee_value_rate") in ("Per Annum", "Per Frequency")
    assert fee.findtext("fee_start/start_type") == "Immediately"
    assert fee.findtext("fee_end/end_type") == "Maturity"
    # stream_applicable references a declared plan_stream_key
    stream_key = root.find("content/quotations/quotation/plan_stream")
    applicable = fee.find("stream_applicable")
    assert applicable is not None and stream_key is not None
    assert applicable.get("plan_stream_key") == stream_key.get("plan_stream_key")


def test_xml_is_deterministic() -> None:
    assert gi.build_illustration_xml(_inputs()) == gi.build_illustration_xml(_inputs())


def test_inputs_from_contact_maps_crm_codes() -> None:
    contact = {
        "contactid": "guid-1",
        "cam_externalclientid": "GRAPHENE-TEST-0003",
        "cam_title": 809130004,  # Dr
        "firstname": "Grace",
        "lastname": "Fairweather",
        "gendercode": 2,
        "familystatuscode": 5,  # Civil Partner
        "cam_birthdate": "1957-01-27T00:00:00Z",
        "cam_retirementageint": 68,
    }
    inputs = gi.inputs_from_contact(contact, "2026-07-14")
    assert inputs.title == "Dr"
    assert inputs.sex == "Female"
    assert inputs.marital_status == "Civil Partnership"
    assert inputs.date_of_birth == "1957-01-27"
    assert inputs.exit_age == 68
    assert inputs.contact_id == "guid-1"
    assert inputs.fund_value == 150_000.0 + 3 * 37_500.0


def test_pdf_renders_and_projections_behave() -> None:
    inputs = _inputs()
    pdf = gi.render_illustration_pdf(inputs)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1_000
    # projections: higher growth never yields a lower fund value
    for years in (1, 10, 20):
        low, mid, high = (gi.project_fund(inputs, rate, years) for rate in (2.0, 5.0, 8.0))
        assert low <= mid <= high
    assert gi.project_fund(inputs, 5.0, 1) < inputs.fund_value * 1.05


def test_parse_ctc_response_success_and_error() -> None:
    success = (
        "<message><header><mode>Response</mode><response>SUCCESS</response></header>"
        "<content><results><document_location>https://cam.ctc.uk.com/out/ABC.pdf"
        "</document_location></results></content></message>"
    )
    location, errors = gi.parse_ctc_response(success)
    assert location == "https://cam.ctc.uk.com/out/ABC.pdf"
    assert errors == []

    error = (
        "<message><header><response>ERROR</response>"
        "<errors><error>Unknown Product Code</error></errors></header></message>"
    )
    location, errors = gi.parse_ctc_response(error)
    assert location is None
    assert errors == ["Unknown Product Code"]

    location, errors = gi.parse_ctc_response("not xml at all")
    assert location is None and errors and "unparseable" in errors[0]


def test_is_test_contact_filter() -> None:
    assert gi.is_test_contact({"cam_externalclientid": "GRAPHENE-TEST-0001"})
    assert gi.is_test_contact({"emailaddress1": "ml-test-004@example.grapheneplatforms.com"})
    assert not gi.is_test_contact({"emailaddress1": "someone@real-firm.co.uk"})
