"""Branded audit PDF of an onboarding journey.

The download on the journey page: the questions that were asked and the answers given, the
account being opened, the declarations the client was shown, and a signature block for each
applicant who accepted them. Generated when it is downloaded. Written declarations are the
snapshot filed when the step was created, so a later edit of the flow does not rewrite what
this client agreed to. A PDF term is named, not embedded.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.branding import parse_lenient
from app.contacts import phone_expr
from app.content import sanitise_html
from app.render.illustration_brand import brand_for_illustration

logger = logging.getLogger(__name__)

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_SKIPPED = frozenset({"skipped", "cancelled"})

_VALUE_LABELS = {
    "uk": "UK",
    "international": "International",
    "cash": "Cash",
    "in_specie": "In-specie",
    "discretionary": "Discretionary",
    "advisory": "Advisory",
    "execution_only": "Execution only",
    "reinvest": "Reinvest",
    "pay_away": "Pay away",
    "individual": "Individual",
    "organisation": "Organisation",
    "trust": "Trust",
    "joint": "Joint",
    "portal": "Portal",
    "in_person": "In person",
    "percentage": "Percentage",
    "fixed": "Fixed",
    "tiered": "Tiered",
    "ongoing": "Ongoing",
    "initial": "Initial",
    "management": "Management",
    "custody": "Custody",
    "adviser": "Adviser",
}

_BANK_LABELS = {
    "name": "Bank account name",
    "account_name": "Bank account name",
    "bank": "Bank",
    "sort_code": "Sort code",
    "account_number": "Account number",
    "iban": "IBAN",
    "bic": "BIC / SWIFT",
}
_ADDRESS_KEYS = frozenset(
    {"line1", "address_line1", "line2", "address_line2", "postcode", "post_code", "city", "town"}
)
_BANK_KEYS = frozenset(_BANK_LABELS)
_TRANSFER_LABELS = {
    "transfer_type": "Transfer type",
    "amount": "Amount",
    "provider_name": "Provider",
    "provider_reference": "Provider reference",
    "safeguarded_benefits": "Safeguarded rights",
    "fca_reference": "FCA reference",
}
_CONTACT_FIELDS = (
    ("title", "Title"),
    ("first_name", "First name"),
    ("middle_name", "Middle name"),
    ("surname", "Surname"),
    ("dob", "Date of birth"),
    ("nationality", "Nationality"),
    ("tax_residency", "Tax residency"),
    ("national_identifier", "National identifier"),
    ("national_identifier_type", "National identifier type"),
    ("email", "Email"),
    ("phone", "Phone number"),
)


@dataclass(frozen=True)
class Row:
    label: str
    value: str


@dataclass(frozen=True)
class Block:
    heading: str | None
    rows: tuple[Row, ...]


@dataclass(frozen=True)
class Chapter:
    title: str
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class FeeLine:
    name: str
    category: str
    frequency: str
    basis: str
    rate: str


@dataclass(frozen=True)
class AccountSection:
    heading: str
    name: str
    facts: tuple[Row, ...]
    fees: tuple[FeeLine, ...]
    chapters: tuple[Chapter, ...]


@dataclass(frozen=True)
class PersonSection:
    name: str
    kind: str
    uid: str | None
    chapters: tuple[Chapter, ...]
    record: tuple[Row, ...]
    identity: str | None
    aml: str | None


@dataclass(frozen=True)
class Declaration:
    title: str
    html: str
    pdf: bool


@dataclass(frozen=True)
class Signature:
    name: str
    kind: str
    uid: str | None
    accepted: bool
    signed_on: str | None
    stamp: str | None
    channel: str | None
    identity: str | None
    aml: str | None


@dataclass(frozen=True)
class ApplicationPack:
    firm_name: str
    uid: str
    status: str
    custody: str | None
    adviser_name: str | None
    adviser_email: str | None
    created_on: str
    generated_on: str
    support_email: str | None = None
    support_phone: str | None = None
    logo: str | None = None
    people: tuple[PersonSection, ...] = ()
    accounts: tuple[AccountSection, ...] = ()
    chapters: tuple[Chapter, ...] = ()
    declarations: tuple[Declaration, ...] = ()
    signatures: tuple[Signature, ...] = ()


def _words(value: object) -> str:
    text_value = str(value or "").replace("_", " ").strip()
    if not text_value:
        return ""
    known = _VALUE_LABELS.get(text_value.lower())
    if known:
        return known
    return text_value[:1].upper() + text_value[1:]


def _long_date(value: date) -> str:
    return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _stamp(value: datetime) -> tuple[str, str]:
    moment = _as_utc(value)
    return _long_date(moment.date()), moment.strftime("%Y-%m-%d %H:%M:%S UTC")


def _json_obj(value: object) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if isinstance(value, Mapping):
        return dict(value)
    return None


def _option_labels(options: object) -> dict[str, str]:
    raw = options
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    items = raw.get("options") if isinstance(raw, Mapping) else raw
    labels: dict[str, str] = {}
    if isinstance(items, list):
        for item in items:
            if isinstance(item, Mapping) and item.get("value") is not None:
                labels[str(item["value"])] = str(item.get("label") or item["value"])
    return labels


def _number(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:g}"
    return None


def _format_date(value: str) -> str | None:
    match = _DATE.match(value)
    if match is None:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return _long_date(date(year, month, day))
    except ValueError:
        return None


def _format_address(value: Mapping[str, Any]) -> str:
    country = value.get("country")
    parts = [
        value.get("line1") or value.get("address_line1"),
        value.get("line2") or value.get("address_line2"),
        value.get("line3") or value.get("address_line3"),
        value.get("city") or value.get("town") or value.get("posttown"),
        value.get("region") or value.get("county"),
        value.get("postcode") or value.get("post_code"),
        _words(country) if isinstance(country, str) else None,
    ]
    return ", ".join(str(part).strip() for part in parts if isinstance(part, str) and part.strip())


def _format_scalar(value: object, options: Mapping[str, str]) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    number = _number(value)
    if number is not None:
        return options.get(number, number)
    if isinstance(value, datetime):
        return _stamp(value)[0]
    if isinstance(value, date):
        return _long_date(value)
    text_value = str(value).strip()
    if not text_value:
        return None
    dated = _format_date(text_value)
    if dated:
        return dated
    if text_value in options:
        return options[text_value]
    return _VALUE_LABELS.get(text_value, text_value)


def _expand(
    key: str,
    value: object,
    *,
    label: str,
    section: str | None,
    options: Mapping[str, str],
) -> list[tuple[str | None, str, str]]:
    if isinstance(value, Mapping):
        record = dict(value)
        if _ADDRESS_KEYS & set(record):
            line = _format_address(record)
            return [(section, label, line)] if line else []
        if _BANK_KEYS & set(record):
            rows: list[tuple[str | None, str, str]] = []
            for child, child_label in _BANK_LABELS.items():
                shown = _format_scalar(record.get(child), {})
                if shown:
                    rows.append((section, child_label, shown))
            return rows
        rows = []
        for child, child_value in record.items():
            rows.extend(
                _expand(
                    f"{key}.{child}",
                    child_value,
                    label=_words(child),
                    section=section,
                    options={},
                )
            )
        return rows
    if isinstance(value, list):
        if value and all(isinstance(item, Mapping) and "number" in item for item in value):
            parts = []
            for item in value:
                number = str(item.get("number") or "").strip()
                if not number:
                    continue
                name = str(item.get("label") or "").strip()
                parts.append(f"{_words(name)} {number}".strip() if name else number)
            return [(section, label, ", ".join(parts))] if parts else []
        joined = [
            item_text for item in value if (item_text := _format_scalar(item, options)) is not None
        ]
        return [(section, label, ", ".join(joined))] if joined else []
    shown = _format_scalar(value, options)
    return [(section, label, shown)] if shown else []


def chapter_for(
    title: str,
    answers: object,
    fields: Sequence[Mapping[str, Any]] = (),
    *,
    participant: str | None = None,
) -> Chapter | None:
    """One form's questions and answers, in the form's own order, labelled as authored."""
    payload = _json_obj(answers) if not isinstance(answers, Mapping) else dict(answers)
    if not payload:
        return None
    specs = sorted(
        (field for field in fields if field.get("field_key")),
        key=lambda field: (int(field.get("sort_order") or 0), str(field.get("field_key"))),
    )
    known = {str(field["field_key"]) for field in specs}
    rows: list[tuple[str | None, str, str]] = []
    for field in specs:
        key = str(field["field_key"])
        if key not in payload:
            continue
        rows.extend(
            _expand(
                key,
                payload[key],
                label=str(field.get("label") or "").strip() or _words(key),
                section=(str(field["section"]).strip() if field.get("section") else None) or None,
                options=_option_labels(field.get("options")),
            )
        )
    for key in sorted(set(payload) - known):
        rows.extend(_expand(key, payload[key], label=_words(key), section=None, options={}))
    blocks = _blocks(rows)
    if not blocks:
        return None
    heading = title.strip() or "Answers"
    if participant and participant not in heading:
        heading = f"{heading} — {participant}"
    return Chapter(title=heading, blocks=blocks)


def _blocks(rows: Sequence[tuple[str | None, str, str]]) -> tuple[Block, ...]:
    blocks: list[Block] = []
    for section, label, value in rows:
        current = blocks[-1] if blocks else None
        row = Row(label=label, value=value)
        if current is not None and current.heading == section:
            blocks[-1] = Block(heading=section, rows=(*current.rows, row))
        else:
            blocks.append(Block(heading=section, rows=(row,)))
    return tuple(blocks)


def _covered(answers: Sequence[object]) -> set[str]:
    keys: set[str] = set()
    for answers_value in answers:
        payload = answers_value if isinstance(answers_value, Mapping) else _json_obj(answers_value)
        if not payload:
            continue
        for key, value in payload.items():
            name = str(key)
            keys.add(name)
            if "." in name:
                parent, child = name.split(".", 1)
                keys.add(parent)
                keys.add(child)
            record = _json_obj(value)
            if record:
                keys.update(str(child) for child in record)
    return keys


def _join_list(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        parts = [_words(item) if isinstance(item, str) else str(item) for item in value if item]
        return ", ".join(part for part in parts if part) or None
    return None


def contact_record(profile: Mapping[str, Any], covered: set[str]) -> tuple[Row, ...]:
    """Contact facts the forms did not already ask, so a saved bank detail is not lost."""
    rows: list[Row] = []
    for key, label in _CONTACT_FIELDS:
        if key in covered:
            continue
        value = profile.get(key)
        if key in {"nationality", "tax_residency"}:
            shown = _join_list(value)
        elif key == "dob" and value is not None:
            shown = _long_date(value) if isinstance(value, date) else _format_date(str(value))
        else:
            shown = _format_scalar(value, {})
        if shown:
            rows.append(Row(label=label, value=shown))
    address = _json_obj(profile.get("address"))
    if address and not (_ADDRESS_KEYS & covered) and "address" not in covered:
        line = _format_address(address)
        if line:
            rows.append(Row(label="Address", value=line))
    bank = _json_obj(profile.get("bank_account"))
    if bank and not (_BANK_KEYS & covered) and "bank_account" not in covered:
        for child, label in _BANK_LABELS.items():
            shown = _format_scalar(bank.get(child), {})
            if shown:
                rows.append(Row(label=label, value=shown))
    return tuple(rows)


def _statuses(steps: Sequence[Mapping[str, Any]], step_type: str) -> list[str]:
    return [str(step.get("status") or "") for step in steps if step.get("step_type") == step_type]


def _check(statuses: Sequence[str]) -> str | None:
    if not statuses:
        return None
    live = [status for status in statuses if status not in _SKIPPED]
    if not live:
        return "Not required"
    if any(status == "failed" for status in live):
        return "No"
    if all(status == "fulfilled" for status in live):
        return "Yes"
    return "Pending"


def _fee_line(fee: Mapping[str, Any]) -> FeeLine | None:
    name = str(fee.get("name") or "").strip()
    if not name:
        return None
    rule = _json_obj(fee.get("rule")) or {}
    basis = str(fee.get("fee_type") or "")
    rate = "—"
    if basis == "percentage":
        number = _number(rule.get("rate"))
        if number is not None:
            rate = f"{number}%"
    elif basis == "fixed":
        number = _number(rule.get("amount"))
        if number is not None:
            rate = number
    elif basis == "tiered":
        rate = "Tiered"
    return FeeLine(
        name=name,
        category=_words(fee.get("category")) or "—",
        frequency=_words(fee.get("frequency")) or "—",
        basis=_words(basis) or "—",
        rate=rate,
    )


def _transfer_rows(transfer: object) -> tuple[Row, ...]:
    record = _json_obj(transfer)
    if not record or not any(record.get(key) not in (None, "", False) for key in _TRANSFER_LABELS):
        return ()
    rows: list[Row] = []
    for key, label in _TRANSFER_LABELS.items():
        shown = _format_scalar(record.get(key), {})
        if shown:
            rows.append(Row(label=label, value=shown))
    return tuple(rows)


def _step_contact(step: Mapping[str, Any]) -> int | None:
    participant = step.get("participant_subject_id")
    if participant is not None:
        return int(participant)
    if step.get("kind") == "profile" and step.get("subject_id") is not None:
        return int(step["subject_id"])
    return None


def _declaration_for(
    step: Mapping[str, Any], acceptances: Sequence[Mapping[str, Any]]
) -> Declaration | None:
    if step.get("step_type") != "accept_terms" or step.get("status") in _SKIPPED:
        return None
    term_uid = str(step.get("term_uid") or "") or None
    matched = [
        row
        for row in acceptances
        if term_uid and str(row.get("term_uid") or "") == term_uid and row.get("body_html")
    ]
    html_body = str(step.get("terms_html") or "").strip()
    if not html_body and matched:
        html_body = str(matched[-1].get("body_html") or "").strip()
    pdf = bool(step.get("terms_file")) or (
        not html_body and any(str(row.get("body_kind") or "") == "pdf" for row in matched)
    )
    title = (
        str(step.get("document_name") or "").strip()
        or (str(matched[-1].get("title")).strip() if matched else "")
        or str(step.get("title") or "").strip()
        or "Declaration"
    )
    return Declaration(title=title, html=html_body, pdf=pdf)


def assemble_pack(
    *,
    firm_name: str,
    uid: str,
    status: str | None,
    custody: str | None,
    adviser_name: str | None,
    adviser_email: str | None,
    created_at: datetime | None,
    generated_at: datetime,
    support_email: str | None = None,
    support_phone: str | None = None,
    logo: str | None = None,
    profiles: Sequence[Mapping[str, Any]] = (),
    accounts: Sequence[Mapping[str, Any]] = (),
    steps: Sequence[Mapping[str, Any]] = (),
    fields_by_form: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    fees_by_account: Mapping[int, Sequence[Mapping[str, Any]]] | None = None,
    acceptances: Sequence[Mapping[str, Any]] = (),
) -> ApplicationPack:
    """The printable pack. Pure: the loader fetches, this decides what the page says."""
    forms = fields_by_form or {}
    fees = fees_by_account or {}
    by_case: dict[int, list[Mapping[str, Any]]] = {}
    for step in steps:
        by_case.setdefault(int(step.get("case_id") or 0), []).append(step)

    people: list[PersonSection] = []
    for profile in profiles:
        case_steps = by_case.get(int(profile.get("case_id") or 0), [])
        chapters = tuple(
            chapter
            for step in case_steps
            if step.get("step_type") == "fill_form"
            and (
                chapter := chapter_for(
                    str(step.get("title") or "Answers"),
                    step.get("answers"),
                    forms.get(str(step.get("form_uid") or ""), ()),
                    participant=(
                        str(step["participant_name"]) if step.get("participant_name") else None
                    ),
                )
            )
            is not None
        )
        covered = _covered(
            [step.get("answers") for step in case_steps if step.get("step_type") == "fill_form"]
        )
        people.append(
            PersonSection(
                name=str(profile.get("name") or "Applicant"),
                kind=_words(profile.get("contact_type")) or "Applicant",
                uid=str(profile["uid"]) if profile.get("uid") else None,
                chapters=chapters,
                record=contact_record(profile, covered),
                identity=_check(_statuses(case_steps, "verify_identity")),
                aml=_check(_statuses(case_steps, "aml_check")),
            )
        )

    account_sections: list[AccountSection] = []
    for index, account in enumerate(accounts, start=1):
        product = str(account.get("product_type") or account.get("product_name") or "Account")
        short = product.isalpha() and len(product) <= 8
        product_label = product.upper() if short else _words(product)
        facts: list[Row] = [Row(label="Product type", value=product_label)]
        if account.get("name"):
            facts.append(Row(label="Account name", value=str(account["name"])))
        account_custody = account.get("custody") or custody
        if account_custody:
            facts.append(Row(label="Custody", value=_words(account_custody)))
        raw_parties = account.get("parties")
        parties = raw_parties if isinstance(raw_parties, list) else []
        if len(parties) > 1:
            facts.append(Row(label="Applicant type", value="Joint"))
        elif parties:
            facts.append(Row(label="Applicant type", value=_words(parties[0].get("contact_type"))))
        for key, label in (
            ("currency", "Currency"),
            ("investment_capacity", "Investment capacity"),
            ("model_name", "Model"),
            ("dividend_treatment", "Dividend treatment"),
        ):
            shown = _format_scalar(account.get(key), {})
            if shown and key == "currency":
                shown = shown.upper()
            if shown:
                facts.append(Row(label=label, value=shown))
        facts.extend(_transfer_rows(account.get("transfer")))
        case_steps = by_case.get(int(account.get("case_id") or 0), [])
        chapters = tuple(
            chapter
            for step in case_steps
            if step.get("step_type") == "fill_form"
            and (
                chapter := chapter_for(
                    str(step.get("title") or "Account details"),
                    step.get("answers"),
                    forms.get(str(step.get("form_uid") or ""), ()),
                    participant=(
                        str(step["participant_name"]) if step.get("participant_name") else None
                    ),
                )
            )
            is not None
        )
        fee_lines = tuple(
            line
            for fee in fees.get(int(account.get("account_id") or 0), ())
            if (line := _fee_line(fee)) is not None
        )
        account_sections.append(
            AccountSection(
                heading=f"Account {index} — {product_label}",
                name=str(account.get("name") or product_label),
                facts=tuple(facts),
                fees=fee_lines,
                chapters=chapters,
            )
        )

    application_chapters = tuple(
        chapter
        for step in steps
        if step.get("kind") == "application"
        and step.get("step_type") == "fill_form"
        and (
            chapter := chapter_for(
                str(step.get("title") or "Answers"),
                step.get("answers"),
                forms.get(str(step.get("form_uid") or ""), ()),
            )
        )
        is not None
    )

    declarations: list[Declaration] = []
    seen: set[str] = set()
    for step in steps:
        declaration = _declaration_for(step, acceptances)
        if declaration is None:
            continue
        key = str(step.get("term_uid") or "") or (
            f"{declaration.title}:{declaration.html}:{declaration.pdf}"
        )
        if key in seen:
            continue
        seen.add(key)
        declarations.append(declaration)

    people_by_id = {
        int(profile["contact_id"]): person
        for profile, person in zip(profiles, people, strict=True)
        if profile.get("contact_id") is not None
    }
    terms_by_person: dict[int, list[Mapping[str, Any]]] = {}
    for step in steps:
        if step.get("step_type") != "accept_terms" or step.get("status") in _SKIPPED:
            continue
        contact_id = _step_contact(step)
        if contact_id is not None:
            terms_by_person.setdefault(contact_id, []).append(step)

    signatures: list[Signature] = []
    for contact_id, term_steps in terms_by_person.items():
        person = people_by_id.get(contact_id)
        name = person.name if person else str(term_steps[0].get("participant_name") or "Applicant")
        kind = person.kind if person else "Applicant"
        person_uid = person.uid if person else None
        accepted = all(str(step.get("status") or "") == "fulfilled" for step in term_steps)
        term_uids = {str(step.get("term_uid") or "") for step in term_steps if step.get("term_uid")}
        matched = [
            row
            for row in acceptances
            if term_uids
            and int(row.get("contact_id") or 0) == contact_id
            and str(row.get("term_uid") or "") in term_uids
            and isinstance(row.get("accepted_at"), datetime)
        ]
        moment = matched[-1]["accepted_at"] if matched else None
        if moment is None:
            completed = [
                step["completed_at"]
                for step in term_steps
                if isinstance(step.get("completed_at"), datetime)
            ]
            moment = max(completed) if completed else None
        signed_on, stamp = _stamp(moment) if isinstance(moment, datetime) else (None, None)
        channels = {_words(row.get("channel")) for row in matched if row.get("channel")}
        channel: str | None
        if channels == {"Portal", "In person"}:
            channel = "Portal and in person"
        elif len(channels) == 1:
            channel = next(iter(channels))
        else:
            channel = None
        signatures.append(
            Signature(
                name=name,
                kind=kind,
                uid=person_uid,
                accepted=accepted,
                signed_on=signed_on if accepted else None,
                stamp=stamp if accepted else None,
                channel=channel if accepted else None,
                identity=person.identity if person else None,
                aml=person.aml if person else None,
            )
        )

    created = _stamp(created_at)[0] if isinstance(created_at, datetime) else "—"
    return ApplicationPack(
        firm_name=firm_name.strip() or "Application",
        uid=uid,
        status=_words(status) or "—",
        custody=_words(custody) if custody else None,
        adviser_name=adviser_name,
        adviser_email=adviser_email,
        created_on=created,
        generated_on=_stamp(generated_at)[0],
        support_email=(support_email or "").strip() or None,
        support_phone=(support_phone or "").strip() or None,
        logo=logo,
        people=tuple(people),
        accounts=tuple(account_sections),
        chapters=application_chapters,
        declarations=tuple(declarations),
        signatures=tuple(signatures),
    )


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _h2(text: str) -> str:
    return f"<h2>{_esc(text)}</h2>"


# Drawn, not a glyph: the PDF face has no check mark, so a character there prints as an empty box.
_TICK = (
    '<svg class="tick" xmlns="http://www.w3.org/2000/svg" width="11" height="11" '
    'viewBox="0 0 12 12">'
    '<rect width="12" height="12" rx="2" fill="#1f9d55"/>'
    '<path d="M2.6 6.2 L5 8.5 L9.5 3.5" fill="none" stroke="#fff" stroke-width="1.7" '
    'stroke-linecap="round" stroke-linejoin="round"/>'
    "</svg>"
)


def _pill(text: str, kind: str = "ok") -> str:
    return f'<span class="pill {kind}">{_esc(text)}</span>'


def _card(inner: str) -> str:
    return f'<div class="card">{inner}</div>' if inner else ""


def _band(text: str) -> str:
    return f'<h3 class="band">{_esc(text)}</h3>'


def _flat(chapters: Sequence[Chapter]) -> list[Row]:
    rows: list[Row] = []
    for chapter in chapters:
        for block in chapter.blocks:
            rows.extend(block.rows)
    return rows


def _card_head(title: str, sub: str, badge: str = "") -> str:
    sub_html = f'<div class="sub">{_esc(sub)}</div>' if sub else ""
    return (
        '<table class="card-head"><tr>'
        f"<td><strong>{_esc(title)}</strong>{sub_html}</td>"
        f'<td class="badge">{badge}</td>'
        "</tr></table>"
    )


def _logo_src(logo: str | None) -> str | None:
    if not logo or "<" in logo or '"' in logo or ">" in logo:
        return None
    if logo.startswith("data:image/") and "base64," in logo:
        return logo
    if logo.startswith("https://") and " " not in logo:
        return logo
    return None


def _facts(rows: Sequence[Row]) -> str:
    if not rows:
        return ""
    body = "".join(f"<tr><th>{_esc(row.label)}</th><td>{_esc(row.value)}</td></tr>" for row in rows)
    return f'<table class="facts">{body}</table>'


_TRANSFER_ROW_LABELS = frozenset(_TRANSFER_LABELS.values())


def _acceptance_table(pack: ApplicationPack) -> str:
    signed = {signature.name: signature for signature in pack.signatures}
    body: list[str] = []
    for person in pack.people:
        signature = signed.get(person.name)
        if signature and signature.accepted:
            status = _pill("Accepted")
            when = _esc(signature.signed_on or "")
        elif signature:
            status = _pill("Not yet signed", "mute")
            when = ""
        else:
            status, when = "—", ""
        body.append(
            "<tr>"
            f"<td><strong>{_esc(person.name)}</strong>"
            f'<div class="sub">{_esc(person.kind)}</div></td>'
            f"<td>{status}</td><td>{when}</td></tr>"
        )
    if not body:
        return ""
    return (
        _h2("Applicant acceptance summary") + '<table class="people"><thead><tr>'
        "<th>Applicant</th><th>Status</th><th>Signed</th>"
        f"</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def _person_card(person: PersonSection, signature: Signature | None) -> str:
    badge = _pill("Terms accepted") if signature and signature.accepted else ""
    rows = _flat(person.chapters) + list(person.record)
    if person.identity:
        rows.append(Row(label="Identity verified", value=person.identity))
    if person.aml:
        rows.append(Row(label="AML approved", value=person.aml))
    sub = " · ".join(part for part in (person.kind, person.uid) if part)
    empty = ""
    if not rows:
        empty = '<p class="note">No answers have been recorded for this applicant yet.</p>'
    return _card(_card_head(person.name, sub, badge) + _facts(rows) + empty)


def _fee_block(fee: FeeLine) -> str:
    return f"<h3>{_esc(fee.name)}</h3>" + _facts(
        (
            Row(label="Category", value=fee.category),
            Row(label="Frequency", value=fee.frequency),
            Row(label="Type", value=fee.basis),
            Row(label="Package name", value=fee.name),
            Row(label="Rate", value=fee.rate),
        )
    )


def _account_block(account: AccountSection) -> str:
    transfer = [row for row in account.facts if row.label in _TRANSFER_ROW_LABELS]
    facts = [row for row in account.facts if row.label not in _TRANSFER_ROW_LABELS]
    facts.extend(_flat(account.chapters))
    currency = next((row.value for row in facts if row.label == "Currency"), "")
    product = next((row.value for row in facts if row.label == "Product type"), "")
    party = next((row.value for row in facts if row.label == "Applicant type"), "")
    sub = " · ".join(part for part in (product, party) if part)
    badge = _pill(currency, "mute") if currency else ""
    cash = ""
    if transfer:
        shown = [
            Row(
                label="Initial cash amount" if row.label == "Amount" else row.label,
                value=row.value,
            )
            for row in transfer
        ]
        cash = _band("Initial cash deposit") + _card(_facts(shown))
    fees = ""
    if account.fees:
        fees = _band("Fees") + _card("".join(_fee_block(fee) for fee in account.fees))
    return (
        _h2(account.heading)
        + _card(_card_head(account.name, sub, badge) + _facts(facts))
        + cash
        + fees
    )


def _declaration_block(declaration: Declaration) -> str:
    body = sanitise_html(declaration.html) if declaration.html else ""
    if body:
        copy = f'<div class="terms">{body}</div>'
    elif declaration.pdf:
        copy = (
            '<p class="note">This declaration was provided as a PDF. '
            "The file is held with the client's documents.</p>"
        )
    else:
        copy = '<p class="note">The wording of this declaration is not on file.</p>'
    return f"<section><h3>{_esc(declaration.title)}</h3>{copy}</section>"


def _signature_block(signature: Signature) -> str:
    who = " · ".join(part for part in (signature.kind, signature.uid) if part)
    if not signature.accepted:
        return (
            '<section class="sign">'
            + _card_head(signature.name, who, _pill("Not yet signed", "mute"))
            + '<p class="note">The declarations in this document have not all been accepted.</p>'
            + "</section>"
        )
    items = [
        "Confirmed acceptance of the declarations set out in this document",
        "Confirmed acceptance of the terms and conditions set out in this document",
    ]
    if signature.identity == "Yes":
        items.append("Identity verified")
    if signature.aml == "Yes":
        items.append("AML check approved")
    when = []
    if signature.signed_on:
        when.append(
            f"<div><span>Signed on</span><strong>{_esc(signature.signed_on)}</strong></div>"
        )
    if signature.stamp:
        when.append(
            f"<div><span>Timestamp (UTC)</span><strong>{_esc(signature.stamp)}</strong></div>"
        )
    if signature.channel:
        when.append(f"<div><span>Channel</span><strong>{_esc(signature.channel)}</strong></div>")
    checks = "".join(f'<li class="check">{_TICK}{_esc(item)}</li>' for item in items)
    return (
        '<section class="sign accepted">'
        + _card_head(signature.name, who, _pill("Accepted", "solid"))
        + f"<ul>{checks}</ul>"
        + f'<p class="signoff">Signed by <span class="autograph">{_esc(signature.name)}</span></p>'
        + f'<div class="when">{"".join(when)}</div></section>'
    )


def render_application_html(pack: ApplicationPack) -> str:
    """The document body. Every value is escaped; declaration HTML is sanitised again here.

    The page follows the account-opening application: a running header, a letterhead, then the
    summary, the applicant's acceptance, one card per client, each account with its deposit and
    fees, the declarations, and a signature card.
    """
    logo = _logo_src(pack.logo)
    logo_html = f'<img class="logo" alt="" src="{_esc(logo)}">' if logo else ""
    # The running header's top left: the firm's logo when it has one, else its name.
    firm_mark = (
        f'<img class="mini" alt="{_esc(pack.firm_name)}" src="{_esc(logo)}">'
        if logo
        else _esc(pack.firm_name)
    )
    support = " · ".join(part for part in (pack.support_email, pack.support_phone) if part)
    summary = [Row(label="Application UID", value=pack.uid)]
    if pack.custody:
        summary.append(Row(label="Custody", value=pack.custody))
    summary.append(Row(label="Status", value=pack.status))
    if pack.adviser_name:
        summary.append(Row(label="Adviser", value=pack.adviser_name))
    if pack.adviser_email and pack.adviser_email != pack.adviser_name:
        summary.append(Row(label="Adviser email", value=pack.adviser_email))
    summary.append(Row(label="Firm", value=pack.firm_name))
    summary.append(Row(label="Created", value=pack.created_on))
    summary.append(Row(label="Accounts", value=str(len(pack.accounts))))

    signed = {signature.name: signature for signature in pack.signatures}
    people = "".join(_person_card(person, signed.get(person.name)) for person in pack.people)
    people_html = _h2("Client data") + people if people else ""
    questions = _flat(pack.chapters)
    questions_html = _h2("Application") + _card(_facts(questions)) if questions else ""
    terms = (
        _h2("Declarations and terms")
        + "".join(_declaration_block(item) for item in pack.declarations)
        if pack.declarations
        else ""
    )
    signed_block = ""
    if pack.signatures:
        signed_block = (
            _h2("Client acceptance")
            + '<p class="note">Per-applicant record of acceptance of the declarations and terms '
            "set out in this document, captured when they completed the onboarding.</p>"
            + "".join(_signature_block(item) for item in pack.signatures)
        )
    mark = f'<td class="mark">{logo_html}</td>' if logo_html else ""
    return (
        '<article class="application-pack">'
        '<div class="page-rule"></div>'
        f'<div class="pack-firm">{firm_mark}</div>'
        f'<div class="pack-ref">Application {_esc(pack.uid)}</div>'
        f'<div class="pack-when">Generated {_esc(pack.generated_on)}</div>'
        '<header class="letterhead"><table><tr><td>'
        '<p class="kicker">Account opening application</p>'
        f"<h1>{_esc(pack.firm_name)}</h1>"
        f'<p class="meta">Audit copy · generated {_esc(pack.generated_on)}</p>'
        f'<p class="meta">{_esc(support)}</p>'
        f"</td>{mark}</tr></table></header>"
        f"{_h2('Application summary')}"
        f"{_card(_facts(tuple(summary)))}"
        f"{_acceptance_table(pack)}"
        f"{people_html}"
        f"{''.join(_account_block(account) for account in pack.accounts)}"
        f"{questions_html}"
        f"{terms}"
        f"{signed_block}"
        "</article>"
    )


APPLICATION_PACK_CSS = """
@page {
  size: A4;
  margin: 22mm 14mm 16mm;
  @top-left {
    content: element(pack-firm);
    font-family: "Liberation Sans", Arial, sans-serif;
    font-size: 12pt;
    font-weight: 700;
    color: #111827;
    vertical-align: middle;
  }
  @top-center {
    content: element(pack-ref);
    font-family: "Liberation Sans", Arial, sans-serif;
    font-size: 9pt;
    color: #6b7280;
    vertical-align: middle;
  }
  @top-right {
    content: element(pack-when);
    font-family: "Liberation Sans", Arial, sans-serif;
    font-size: 8pt;
    color: #6b7280;
    text-align: right;
    vertical-align: middle;
  }
  @bottom-right {
    content: counter(page);
    font-family: "Liberation Sans", Arial, sans-serif;
    font-size: 8pt;
    color: #9ca3af;
  }
}
.pack-firm { position: running(pack-firm); }
.pack-ref { position: running(pack-ref); }
.pack-when { position: running(pack-when); text-align: right; }
.pack-firm .mini { height: 20px; width: auto; display: block; }
.page-rule {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  border-top: 2.5px solid #111827;
}
.application-pack {
  font-family: "Liberation Sans", Arial, sans-serif;
  font-size: 10pt;
  line-height: 1.4;
  color: #111827;
  padding-top: 8px;
}
.application-pack .letterhead {
  background: #f3f4f6;
  border-left: 6px solid #111827;
  border-radius: 8px;
  padding: 12px 16px;
  margin: 4px 0 8px;
}
.application-pack .letterhead table { width: 100%; border-collapse: collapse; }
.application-pack .letterhead td { border: none; vertical-align: middle; padding: 0; }
.application-pack .letterhead .mark { width: 180px; text-align: right; }
.application-pack .logo { max-height: 52px; max-width: 170px; }
.application-pack .kicker {
  font-size: 8.5pt;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin: 0 0 2px;
}
.application-pack h1 { font-size: 20pt; margin: 0 0 2px; font-weight: 700; }
.application-pack h2 {
  font-size: 14pt;
  font-weight: 700;
  text-decoration: underline;
  text-underline-offset: 3px;
  margin: 16px 0 8px;
  page-break-after: avoid;
}
.application-pack h3 { font-size: 11pt; margin: 10px 0 2px; page-break-after: avoid; }
.application-pack .band {
  font-size: 8.5pt;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin: 14px 0 6px;
  page-break-after: avoid;
}
.application-pack .meta, .application-pack .note, .application-pack .sub {
  color: #6b7280;
  margin: 0 0 2px;
  font-size: 9pt;
}
.application-pack .card {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px 14px 4px;
  margin: 0 0 10px;
}
.application-pack table.card-head { width: 100%; border-collapse: collapse; margin: 0 0 4px; }
.application-pack table.card-head td { border: none; padding: 0 0 6px; vertical-align: top; }
.application-pack table.card-head .badge { width: 150px; text-align: right; }
.application-pack .pill {
  display: inline-block;
  background: #e5f6ec;
  color: #157a3c;
  font-size: 8pt;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
  line-height: 1.4;
}
.application-pack .pill.solid {
  background: #1f9d55;
  color: #ffffff;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.application-pack .pill.mute { background: #f3f4f6; color: #374151; }
.application-pack table.facts { width: 100%; border-collapse: collapse; margin: 0; }
.application-pack table.facts th {
  width: 42%;
  text-align: left;
  font-weight: 400;
  padding: 5px 12px 5px 0;
  vertical-align: top;
  color: #6b7280;
}
.application-pack table.facts td { padding: 5px 0; vertical-align: top; font-weight: 700; }
.application-pack table.facts tr { border-bottom: 1px solid #e5e7eb; }
.application-pack table.people { width: 100%; border-collapse: collapse; margin: 0 0 8px; }
.application-pack table.people th {
  text-align: left;
  font-size: 8pt;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #6b7280;
  font-weight: 600;
  padding: 4px 8px 4px 0;
  border-bottom: 1px solid #e5e7eb;
}
.application-pack table.people td {
  padding: 8px 8px 8px 0;
  border-bottom: 1px solid #e5e7eb;
  vertical-align: top;
}
.application-pack .terms p, .application-pack .terms li { margin: 0 0 6px; }
.application-pack .terms ul, .application-pack .terms ol { padding-left: 1.2em; }
.application-pack .sign {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 12px 14px;
  margin: 0 0 10px;
  page-break-inside: avoid;
}
.application-pack .sign.accepted { border-left: 4px solid #1f9d55; }
.application-pack .sign ul { list-style: none; margin: 8px 0; padding: 0; }
.application-pack .sign li.check { margin: 0 0 4px; }
.application-pack .sign svg.tick {
  width: 11px;
  height: 11px;
  margin-right: 6px;
  vertical-align: -1px;
}
.application-pack .signoff {
  margin: 8px 0 4px;
  color: #6b7280;
  font-size: 8pt;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.application-pack .autograph {
  display: inline-block;
  margin-left: 24px;
  color: #111827;
  font-size: 12pt;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: none;
  text-decoration: underline;
}
.application-pack .when { width: 100%; margin-top: 8px; }
.application-pack .when div { display: inline-block; width: 32%; vertical-align: top; }
.application-pack .when span {
  display: block;
  color: #6b7280;
  font-size: 8pt;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.application-pack .when strong { font-size: 10pt; }
"""


async def render_pack_pdf(pack: ApplicationPack) -> bytes:
    from app.render.renderers import render_pdf

    try:
        pdf, _sha = await render_pdf(
            render_application_html(pack), overlay_css=APPLICATION_PACK_CSS
        )
    except Exception:
        logger.exception("application pack PDF failed for %s", pack.uid)
        raise
    return pdf


def _present_answers(
    answers: object,
    *,
    model_name: str | None,
    fee_names: Sequence[str],
    provider_name: str | None,
) -> object:
    payload = _json_obj(answers)
    if payload is None:
        return answers
    if model_name and "model_id" in payload:
        payload["model_id"] = model_name
    if fee_names and "fee_package_ids" in payload:
        payload["fee_package_ids"] = list(fee_names)
    if provider_name and "transfer_provider" in payload:
        payload["transfer_provider"] = provider_name
    return payload


async def load_application_pack(
    session: AsyncSession, tenant_id: int, exec_uid: str
) -> ApplicationPack | None:
    """The pack for one execution, or None when this firm has no such journey."""
    header = (
        (
            await session.execute(
                text(
                    "SELECT e.id, e.uid, e.status, e.created_at, e.crm_application_id AS app_id, "
                    "       a.status AS application_status, a.custody::text AS custody, "
                    "       t.name AS tenant_name, t.theme, "
                    "       COALESCE(adv.name, adv.email) AS adviser_name, "
                    "       adv.email AS adviser_email "
                    "FROM flows.executions e "
                    "JOIN auth.tenants t ON t.id = e.tenant_id "
                    "LEFT JOIN crm.applications a ON a.id = e.crm_application_id "
                    "LEFT JOIN auth.users adv ON adv.id = a.adviser_id "
                    "WHERE e.uid = :u AND e.tenant_id = :t"
                ),
                {"u": exec_uid, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if header is None:
        return None

    profiles = (
        (
            await session.execute(
                text(
                    "SELECT c.id AS case_id, ct.id AS contact_id, ct.uid, "  # noqa: S608
                    "       ct.type::text AS contact_type, "
                    "       COALESCE(NULLIF(btrim(concat_ws(' ', ct.first_name, ct.surname)), ''), "
                    "                ct.name) AS name, "
                    "       ct.title, ct.first_name, ct.middle_name, ct.surname, ct.dob, "
                    "       ct.nationality, ct.tax_residency, ct.national_identifier, "
                    "       ct.national_identifier_type, ct.email, "
                    f"       {phone_expr('ct.mobile')} AS phone, "
                    "       ct.address, ct.bank_account "
                    "FROM flows.cases c "
                    "JOIN crm.contacts ct ON ct.id = c.subject_id AND c.subject_type = 'contact' "
                    "WHERE c.execution_id = :e AND c.tenant_id = :t AND c.kind = 'profile' "
                    "ORDER BY c.subject_id"
                ),
                {"e": header["id"], "t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    accounts = (
        (
            await session.execute(
                text(
                    "SELECT c.id AS case_id, acct.id AS account_id, acct.name, "
                    "       acct.currency, acct.investment_capacity, acct.dividend_treatment, "
                    "       acct.custody::text AS custody, acct.transfer, "
                    "       p.product_type::text AS product_type, p.name AS product_name, "
                    "       m.name AS model_name "
                    "FROM flows.cases c "
                    "JOIN platform.accounts acct "
                    "  ON acct.id = c.subject_id AND c.subject_type = 'account' "
                    "LEFT JOIN platform.products p ON p.id = acct.product_id "
                    "LEFT JOIN platform.models m ON m.id = acct.model_id "
                    "WHERE c.execution_id = :e AND c.tenant_id = :t AND c.kind = 'account' "
                    "ORDER BY acct.id"
                ),
                {"e": header["id"], "t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    parties: dict[int, list[dict[str, object]]] = {}
    if header["app_id"] is not None:
        party_rows = (
            (
                await session.execute(
                    text(
                        "SELECT ac.account_id, ct.type::text AS contact_type "
                        "FROM crm.account_contacts ac "
                        "JOIN crm.contacts ct ON ct.id = ac.contact_id "
                        "WHERE ac.application_id = :a AND ac.tenant_id = :t "
                        "ORDER BY ac.account_id, ac.role::text"
                    ),
                    {"a": header["app_id"], "t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        for party in party_rows:
            parties.setdefault(int(party["account_id"]), []).append(dict(party))

    steps = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    "SELECT c.id AS case_id, c.kind, c.subject_id, "
                    "       cs.step_type, cs.title, cs.status, cs.completed_at, "
                    "       cs.participant_subject_id, "
                    "       COALESCE(NULLIF(btrim(concat_ws(' ', pc.first_name, pc.surname)), ''), "
                    "                pc.name) AS participant_name, "
                    "       ri.form_uid, fr.answers, "
                    "       td.name AS document_name, "
                    "       NULLIF(td.metadata ->> 'content', '') AS terms_html, "
                    "       NULLIF(td.metadata ->> 'term_uid', '') AS term_uid, "
                    "       (td.id IS NOT NULL AND NULLIF(td.metadata ->> 'content', '') IS NULL) "
                    "         AS terms_file "
                    "FROM flows.cases c "
                    "JOIN flows.case_steps cs ON cs.case_id = c.id "
                    "LEFT JOIN crm.contacts pc ON pc.id = cs.participant_subject_id "
                    "LEFT JOIN documents.request_items ri ON ri.id = cs.request_item_id "
                    "LEFT JOIN LATERAL ("
                    "  SELECT answers FROM documents.form_responses "
                    "  WHERE request_item_id = cs.request_item_id "
                    "  ORDER BY id DESC LIMIT 1"
                    ") fr ON true "
                    "LEFT JOIN documents.documents td "
                    "  ON td.id = ri.target_document_id AND td.tenant_id = :t "
                    "WHERE c.execution_id = :e AND c.tenant_id = :t "
                    "ORDER BY CASE c.kind WHEN 'profile' THEN 0 WHEN 'account' THEN 1 ELSE 2 END, "
                    "         c.subject_id NULLS LAST, cs.id"
                ),
                {"e": header["id"], "t": tenant_id},
            )
        )
        .mappings()
        .all()
    ]
    form_uids = sorted({str(step["form_uid"]) for step in steps if step.get("form_uid")})
    fields_by_form: dict[str, list[dict[str, Any]]] = {}
    if form_uids:
        field_rows = (
            (
                await session.execute(
                    text(
                        "SELECT f.uid AS form_uid, ff.field_key, ff.label, ff.section, "
                        "       ff.sort_order, ff.options "
                        "FROM documents.forms f "
                        "JOIN documents.form_fields ff ON ff.form_id = f.id "
                        "WHERE f.tenant_id = :t AND f.uid = ANY(:uids) "
                        "ORDER BY ff.sort_order, ff.id"
                    ),
                    {"t": tenant_id, "uids": form_uids},
                )
            )
            .mappings()
            .all()
        )
        for field in field_rows:
            fields_by_form.setdefault(str(field["form_uid"]), []).append(dict(field))

    account_ids = [int(account["account_id"]) for account in accounts]
    fees_by_account: dict[int, list[dict[str, Any]]] = {}
    if account_ids:
        fee_rows = (
            (
                await session.execute(
                    text(
                        "SELECT afp.account_id, fp.name, fp.category, "
                        "       fp.frequency::text AS frequency, "
                        "       fp.fee_type::text AS fee_type, fp.rule "
                        "FROM platform.account_fee_packages afp "
                        "JOIN platform.fee_packages fp "
                        "  ON fp.id = afp.fee_package_id AND fp.tenant_id = afp.tenant_id "
                        "WHERE afp.account_id = ANY(:ids) "
                        "ORDER BY afp.sort_order, fp.name"
                    ),
                    {"ids": account_ids},
                )
            )
            .mappings()
            .all()
        )
        for fee in fee_rows:
            fees_by_account.setdefault(int(fee["account_id"]), []).append(dict(fee))

    contact_ids = [int(profile["contact_id"]) for profile in profiles if profile.get("contact_id")]
    acceptance_rows: list[dict[str, Any]] = []
    if contact_ids:
        acceptance_rows = [
            dict(row)
            for row in (
                await session.execute(
                    text(
                        "SELECT a.contact_id, a.accepted_at, a.channel::text AS channel, "
                        "       t.uid AS term_uid, v.title, v.body_kind::text AS body_kind, "
                        "       v.body_html "
                        "FROM terms.acceptances a "
                        "JOIN terms.term_versions v ON v.id = a.term_version_id "
                        "JOIN terms.terms t ON t.id = a.term_id "
                        "WHERE a.tenant_id = :t AND a.contact_id = ANY(:ids) "
                        "ORDER BY a.accepted_at"
                    ),
                    {"t": tenant_id, "ids": contact_ids},
                )
            )
            .mappings()
            .all()
        ]

    accounts_by_case = {int(account["case_id"]): account for account in accounts}
    presented: list[dict[str, Any]] = []
    for step in steps:
        account = accounts_by_case.get(int(step["case_id"]))
        transfer = _json_obj(account["transfer"]) if account is not None else None
        fee_names = (
            [str(fee["name"]) for fee in fees_by_account.get(int(account["account_id"]), ())]
            if account is not None
            else []
        )
        step["answers"] = _present_answers(
            step.get("answers"),
            model_name=(
                str(account["model_name"])
                if account is not None and account.get("model_name")
                else None
            ),
            fee_names=fee_names,
            provider_name=(
                str(transfer["provider_name"])
                if transfer and transfer.get("provider_name")
                else None
            ),
        )
        presented.append(step)

    theme = header["theme"]
    if isinstance(theme, str):
        try:
            theme = json.loads(theme)
        except json.JSONDecodeError:
            theme = None
    theme_obj = theme if isinstance(theme, dict) else None
    client = parse_lenient(theme_obj, "client")
    platform = parse_lenient(theme_obj, "platform")
    brand = await brand_for_illustration(session, tenant_id)
    return assemble_pack(
        firm_name=str(header["tenant_name"] or ""),
        uid=str(header["uid"]),
        status=header["application_status"] or header["status"],
        custody=header["custody"],
        adviser_name=header["adviser_name"],
        adviser_email=header["adviser_email"],
        created_at=header["created_at"],
        generated_at=datetime.now(UTC),
        support_email=client.support.email or platform.support.email,
        support_phone=client.support.phone or platform.support.phone,
        logo=brand.get("logo") if isinstance(brand.get("logo"), str) else None,
        profiles=[dict(profile) for profile in profiles],
        accounts=[
            {**dict(account), "parties": parties.get(int(account["account_id"]), [])}
            for account in accounts
        ],
        steps=presented,
        fields_by_form=fields_by_form,
        fees_by_account=fees_by_account,
        acceptances=acceptance_rows,
    )
