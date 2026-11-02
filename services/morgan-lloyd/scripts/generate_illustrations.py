"""Generate test drawdown illustrations for the Morgan Lloyd DEV CRM.

For each Graphene test contact (created by ``populate_test_data.py``) this:

1. builds a **CTC Bespoke XML v1.6** illustration request (``CTC1`` message —
   the schema in ``graphene-platform/docs/morgan-lloyd/XML_v1.6_Bespoke.pdf``):
   a flexi-access drawdown quotation on a SIPP with an existing fund value,
   a generic 100% fund split, fixed monthly withdrawals, maximum tax-free
   cash and an ongoing adviser fee;
2. POSTs the XML to **CTC's calculation engine** (``RunQuote`` — our IP must
   be whitelisted by CTC) and saves the returned illustration PDF into
   ``.data/illustrations/``; if the engine is unreachable a clearly-watermarked
   local preview PDF is rendered instead (``<ref>.preview.pdf``);
3. creates a **benefit application** (``cam_setmemberdetails``) carrying the
   XML as its ``illustrationTemplate`` — the response's ``ssoUrl`` is the
   member drawdown journey link, printed so you can walk through it.

Run via ``moon run morgan-lloyd-sync:illustrations``. ``--skip-crm`` builds
the XML + preview PDFs from synthetic clients without touching the network;
``--no-ctc`` skips the engine call in live mode.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fpdf import FPDF
from morgan_lloyd.enums import FamilyStatus, Gender, Title

# FamilyStatus -> the schema's <marital_status> vocabulary
_MARITAL = {
    FamilyStatus.SINGLE: "Single",
    FamilyStatus.MARRIED: "Married",
    FamilyStatus.CIVIL_PARTNER: "Civil Partnership",
    FamilyStatus.DIVORCED: "Divorced",
    FamilyStatus.COHABITING: "Partnered",
    FamilyStatus.WIDOWED: "Widowed",
}

_GROWTH_RATES = (2.0, 5.0, 8.0)  # low / mid / high gross growth, % pa
_FUND_CHARGE_PCT = 0.45
_ADVISER_FEE_PCT = 0.75

# CTC calculation engine (XML v1.6 -> illustration PDF). Access is by IP
# whitelist — ask CTC/Morgan Lloyd to whitelist the caller.
CTC_QUOTE_URL = "https://cam.ctc.uk.com/Pages/Remote.aspx?wci=RunQuote&nodb=true"


@dataclass(frozen=True)
class IllustrationInputs:
    """Everything one illustration needs, derived from a CRM contact record."""

    reference: str  # output_reference / file stem, e.g. GRAPHENE-TEST-0001
    title: str
    forename: str
    surname: str
    sex: str  # "Male" / "Female"
    marital_status: str  # schema vocabulary
    date_of_birth: str  # yyyy-mm-dd
    exit_age: int
    fund_value: float
    monthly_withdrawal: float
    effective_date: str  # yyyy-mm-dd
    contact_id: str | None = None  # CRM GUID (None for --skip-crm synthetics)


def inputs_from_contact(contact: dict[str, Any], effective_date: str) -> IllustrationInputs:
    """Map a CRM contact record onto illustration inputs (deterministic: the
    fund value and withdrawal derive from the reference index, not an RNG)."""
    reference = str(contact.get("cam_externalclientid") or contact["contactid"])
    index = int(reference.rsplit("-", 1)[-1]) if reference[-1:].isdigit() else 0
    try:
        title = Title(int(contact.get("cam_title", 0))).name.title()
    except ValueError:
        title = "Mr"
    gender = "Female" if contact.get("gendercode") == int(Gender.FEMALE) else "Male"
    try:
        marital = _MARITAL.get(FamilyStatus(int(contact.get("familystatuscode", 0))), "Unknown")
    except ValueError:
        marital = "Unknown"
    return IllustrationInputs(
        reference=reference,
        title=title,
        forename=str(contact.get("firstname", "Test")),
        surname=str(contact.get("lastname", "Client")),
        sex=gender,
        marital_status=marital,
        date_of_birth=str(contact.get("cam_birthdate", "1960-01-01"))[:10],
        exit_age=int(contact.get("cam_retirementageint") or 65),
        fund_value=150_000.0 + index * 37_500.0,
        monthly_withdrawal=750.0 + index * 125.0,
        effective_date=effective_date,
        contact_id=str(contact["contactid"]) if contact.get("contactid") else None,
    )


def synthetic_inputs(count: int, effective_date: str) -> list[IllustrationInputs]:
    """Deterministic stand-ins for --skip-crm runs."""
    people = (
        ("Mr", "Arthur", "Bramley", "Male", "Married", "1959-04-12"),
        ("Mrs", "Isla", "Carrington", "Female", "Partnered", "1963-09-03"),
        ("Dr", "Grace", "Fairweather", "Female", "Single", "1957-01-27"),
        ("Prof", "Thomas", "Dunmore", "Male", "Widowed", "1961-11-16"),
    )
    return [
        IllustrationInputs(
            reference=f"GRAPHENE-TEST-{index:04d}",
            title=person[0],
            forename=person[1],
            surname=person[2],
            sex=person[3],
            marital_status=person[4],
            date_of_birth=person[5],
            exit_age=65,
            fund_value=150_000.0 + index * 37_500.0,
            monthly_withdrawal=750.0 + index * 125.0,
            effective_date=effective_date,
        )
        for index, person in enumerate(people[:count])
    ]


# --- CTC1 XML -----------------------------------------------------------------


def _child(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    element = ET.SubElement(parent, tag, attrs)
    if text is not None:
        element.text = text
    return element


def build_illustration_xml(inputs: IllustrationInputs) -> str:
    """One CTC Bespoke v1.6 ``Request`` message: SIPP flexi-access drawdown."""
    message = ET.Element("message")
    header = _child(message, "header")
    _child(header, "message_version", "CTC1")
    _child(header, "created_date", f"{inputs.effective_date} 09:00:00")
    _child(header, "mode", "Request")

    content = _child(message, "content")
    quotations = _child(content, "quotations")
    _child(quotations, "effective_date", inputs.effective_date)
    _child(quotations, "output_required", "Yes")
    _child(quotations, "output_reference", inputs.reference)
    _child(quotations, "agent_name", "Graphene Platforms (test)")
    _child(quotations, "client_type", "Individual")

    investor = _child(quotations, "investor")
    _child(investor, "title", inputs.title)
    _child(investor, "forename", inputs.forename)
    _child(investor, "surname", inputs.surname)
    _child(investor, "sex", inputs.sex)
    _child(investor, "life_type", "Applicant")
    _child(investor, "marital_status", inputs.marital_status)
    _child(investor, "date_of_birth", inputs.date_of_birth)
    _child(investor, "exit_age", str(inputs.exit_age))

    quotation = _child(
        quotations,
        "quotation",
        product_ref="SIPPEN",
        product_type="Self Invested Personal Pension",
    )
    _child(quotation, "quotation_type", "Drawdown")

    stream = _child(quotation, "plan_stream", plan_stream_key="FUND1")
    _child(stream, "contribution_type", "Existing Fund Value")
    _child(stream, "amount", f"{inputs.fund_value:.2f}")
    _child(stream, "frequency", "Single")
    _child(stream, "crystallised", "No")

    premium_split = _child(quotation, "premium_split")
    _child(premium_split, "stream_applicable", plan_stream_key="FUND1")
    fund_split = _child(premium_split, "fund_split")
    _child(fund_split, "fund_ref", "GRAPHENE-GENERIC")
    _child(fund_split, "fund_name", "Balanced Portfolio (generic test fund)")
    _child(fund_split, "split_per", "100")
    _child(fund_split, "rate_of_return", "5.0")
    _child(fund_split, "management_charge", f"{_FUND_CHARGE_PCT:.2f}")
    _child(fund_split, "generic_fund", "Yes")

    withdrawals = _child(quotation, "withdrawals")
    _child(withdrawals, "basis", "Fixed")
    _child(withdrawals, "amount", f"{inputs.monthly_withdrawal:.2f}")
    _child(withdrawals, "frequency", "Monthly")
    _child(withdrawals, "start_date", inputs.effective_date)
    _child(withdrawals, "drawdown_type", "Flexible")

    crystallisation = _child(quotation, "crystallisation")
    _child(crystallisation, "drawdown_type", "Flexible")
    _child(crystallisation, "amount", f"{inputs.fund_value:.2f}")
    _child(crystallisation, "frequency", "Single")
    _child(crystallisation, "antytxfcsh", type="Maximum")

    adviser_fees = _child(quotation, "adviser_fees")
    adviser_fee = _child(adviser_fees, "adviser_fee")
    _child(adviser_fee, "stream_applicable", plan_stream_key="FUND1")
    _child(adviser_fee, "fee_basis", "Ongoing")
    _child(adviser_fee, "fee_frequency", "Monthly")
    _child(adviser_fee, "fee_timing", "In Arrears")
    _child(adviser_fee, "fee_value_rate", "Per Annum")
    fee_start = _child(adviser_fee, "fee_start")
    _child(fee_start, "start_type", "Immediately")
    fee_end = _child(adviser_fee, "fee_end")
    _child(fee_end, "end_type", "Maturity")
    _child(adviser_fee, "percentage", f"{_ADVISER_FEE_PCT:.2f}")
    _child(adviser_fee, "plus_vat", "No")

    ET.indent(message)
    return ET.tostring(message, encoding="unicode", xml_declaration=True)


# --- local preview PDF ----------------------------------------------------------


def project_fund(inputs: IllustrationInputs, growth_pct: float, years: int) -> float:
    """Indicative year-end fund value: growth net of charges, minus withdrawals."""
    net_rate = (growth_pct - _FUND_CHARGE_PCT - _ADVISER_FEE_PCT) / 100.0
    value = inputs.fund_value
    for _ in range(years):
        value = max(0.0, value * (1 + net_rate) - inputs.monthly_withdrawal * 12)
    return value


def render_illustration_pdf(inputs: IllustrationInputs) -> bytes:
    """A clearly-marked local preview of the illustration (not engine output)."""
    pdf = FPDF()
    pdf.set_creation_date(datetime(2026, 1, 1))  # deterministic output
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "SIPP Flexi-Access Drawdown Illustration", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(180, 30, 30)
    pdf.cell(
        0,
        6,
        f"GRAPHENE TEST DOCUMENT - locally rendered preview ({inputs.reference}); "
        "not produced by the CTC calculation engine.",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    pdf.set_font("Helvetica", "", 11)
    rows = (
        ("Investor", f"{inputs.title} {inputs.forename} {inputs.surname}"),
        ("Date of birth", inputs.date_of_birth),
        ("Marital status", inputs.marital_status),
        ("Planned exit age", str(inputs.exit_age)),
        ("Effective date", inputs.effective_date),
        ("Product", "Self Invested Personal Pension (SIPPEN)"),
        ("Existing fund value", f"GBP {inputs.fund_value:,.2f}"),
        ("Tax-free cash", "Maximum (25%)"),
        ("Withdrawals", f"GBP {inputs.monthly_withdrawal:,.2f} monthly, flexible drawdown"),
        ("Fund", "Balanced Portfolio (generic test fund), 100%"),
        ("Fund charge", f"{_FUND_CHARGE_PCT:.2f}% pa"),
        ("Ongoing adviser fee", f"{_ADVISER_FEE_PCT:.2f}% pa, monthly in arrears"),
    )
    for label, value in rows:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(55, 7, label)
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 7, value, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Indicative projected fund values", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(30, 7, "Year", border=1)
    for rate in _GROWTH_RATES:
        pdf.cell(45, 7, f"{rate:.0f}% growth pa", border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 10)
    for years in (1, 5, 10, 15, 20):
        pdf.cell(30, 7, str(years), border=1)
        for rate in _GROWTH_RATES:
            pdf.cell(45, 7, f"GBP {project_fund(inputs, rate, years):,.0f}", border=1)
        pdf.ln()

    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(
        0,
        5,
        "Projections are indicative only: growth net of the fund charge and adviser fee, "
        "with withdrawals deducted annually. Figures are synthetic test data generated by "
        "Graphene Platforms and carry no advice or guarantee.",
    )
    return bytes(pdf.output())


# --- CTC engine ------------------------------------------------------------------


def parse_ctc_response(text: str) -> tuple[str | None, list[str]]:
    """(document_location, errors) from a CTC RunQuote response message.

    Success responses carry ``<response>SUCCESS</response>`` and a ``results``
    section whose ``<document_location>`` names the assembled PDF; errors carry
    ``<response>ERROR</response>`` with ``<errors><error>…`` descriptions."""
    try:
        root = ET.fromstring(text)  # noqa: S314 - trusted vendor endpoint, IP-whitelisted
    except ET.ParseError:
        return None, [f"unparseable CTC response: {text[:300]!r}"]
    errors = [e.text.strip() for e in root.iter("error") if e.text and e.text.strip()]
    location = next(
        (e.text.strip() for e in root.iter("document_location") if e.text and e.text.strip()),
        None,
    )
    if location is None:
        state = next((e.text for e in root.iter("response") if e.text), "no response element")
        return None, errors or [f"CTC returned {state!r} with no document_location"]
    return location, errors


async def fetch_ctc_pdf(xml: str, quote_url: str) -> tuple[bytes | None, str]:
    """POST the CTC1 message to the engine; return (pdf bytes, detail).

    The engine responds with a result message naming the generated PDF
    (``document_location``); when that is a URL the PDF is downloaded."""
    import httpx

    async with httpx.AsyncClient(timeout=120) as http:
        response = await http.post(
            quote_url, content=xml.encode(), headers={"Content-Type": "text/xml"}
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if response.content[:4] == b"%PDF":  # engine streamed the PDF directly
            return response.content, "engine returned the PDF directly"
        location, errors = parse_ctc_response(response.text)
        if location is None:
            return None, "; ".join(errors) or f"unexpected CTC response ({content_type})"
        if not location.lower().startswith(("http://", "https://")):
            return None, f"CTC document_location is not a URL: {location!r}"
        document = await http.get(location)
        document.raise_for_status()
        return document.content, f"downloaded {location}"


# --- live wiring -----------------------------------------------------------------


def is_test_contact(contact: dict[str, Any]) -> bool:
    external = str(contact.get("cam_externalclientid") or "")
    email = str(contact.get("emailaddress1") or "")
    return external.startswith("GRAPHENE-TEST") or email.startswith("ml-test-")


def write_preview(out_dir: Path, inputs: IllustrationInputs, *, suffix: str = ".pdf") -> None:
    (out_dir / f"{inputs.reference}{suffix}").write_bytes(render_illustration_pdf(inputs))
    print(f"wrote   {out_dir / inputs.reference}{suffix} (local preview)")


def _write_files(
    out_dir: Path, inputs: IllustrationInputs, xml: str, pdf: bytes | None, detail: str
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{inputs.reference}.xml").write_text(xml)
    if pdf is not None:
        (out_dir / f"{inputs.reference}.pdf").write_bytes(pdf)
        print(f"wrote   {out_dir / inputs.reference}.pdf (CTC engine; {detail})")
    elif detail:
        print(f"ctc     {inputs.reference} failed — {detail}")
        write_preview(out_dir, inputs, suffix=".preview.pdf")
    else:
        write_preview(out_dir, inputs)


async def write_outputs(
    out_dir: Path, inputs: IllustrationInputs, xml: str, ctc_url: str | None
) -> None:
    """The XML always lands; the PDF comes from the CTC engine when *ctc_url*
    is set (falling back to a watermarked local preview on failure)."""
    pdf: bytes | None = None
    detail = ""
    if ctc_url is not None:
        try:
            pdf, detail = await fetch_ctc_pdf(xml, ctc_url)
        except Exception as exc:  # noqa: BLE001 - engine reachability is environmental
            pdf, detail = None, str(exc)
    _write_files(out_dir, inputs, xml, pdf, detail)


async def run_live(count: int, out_dir: Path, effective_date: str, ctc_url: str | None) -> int:
    from morgan_lloyd import MorganLloydClient, MorganLloydError

    failures = 0
    async with MorganLloydClient() as api:
        contacts = [c for c in await api.get_all_contacts() if is_test_contact(c)]
        contacts.sort(key=lambda c: str(c.get("cam_externalclientid") or ""))
        if not contacts:
            print("no GRAPHENE-TEST contacts in the CRM — run populate_test_data.py first")
            return 1
        for contact in contacts[:count]:
            inputs = inputs_from_contact(contact, effective_date)
            xml = build_illustration_xml(inputs)
            await write_outputs(out_dir, inputs, xml, ctc_url)
            try:
                response = await api.create_benefit_application(
                    {
                        "contactId": inputs.contact_id,
                        "advised": True,
                        "assetValuation": inputs.fund_value,
                        "cashValuation": round(inputs.fund_value * 0.05, 2),
                        "crystallisedValue": 0,
                        "illustrationTemplate": xml,
                    }
                )
            except MorganLloydError as exc:
                failures += 1
                print(f"failed  benefit application {inputs.reference} — {exc}")
                continue
            application_id = response.get("benefitApplicationId", "?")
            sso_url = response.get("ssoUrl") or response.get("ssoURL") or "(no ssoUrl returned)"
            print(f"created benefit application {inputs.reference} id={application_id}")
            print(f"        drawdown journey: {sso_url}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--applications", type=int, default=3, help="how many contacts to process")
    parser.add_argument(
        "--out-dir", type=Path, default=Path(".data/illustrations"), help="PDF/XML output directory"
    )
    parser.add_argument(
        "--effective-date", default=date.today().isoformat(), help="quotation effective date"
    )
    parser.add_argument(
        "--skip-crm",
        action="store_true",
        help="build XML + preview PDFs from synthetic clients; no HTTP at all",
    )
    parser.add_argument(
        "--no-ctc",
        action="store_true",
        help="live mode without the CTC engine call (local preview PDFs only)",
    )
    parser.add_argument(
        "--ctc-url", default=CTC_QUOTE_URL, help="CTC RunQuote endpoint (IP-whitelisted)"
    )
    args = parser.parse_args(argv)
    if args.skip_crm:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for inputs in synthetic_inputs(args.applications, args.effective_date):
            (args.out_dir / f"{inputs.reference}.xml").write_text(build_illustration_xml(inputs))
            write_preview(args.out_dir, inputs)
        return 0
    ctc_url = None if args.no_ctc else str(args.ctc_url)
    return asyncio.run(run_live(args.applications, args.out_dir, args.effective_date, ctc_url))


if __name__ == "__main__":
    sys.exit(main())
