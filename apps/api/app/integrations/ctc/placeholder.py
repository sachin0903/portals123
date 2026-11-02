"""A clearly-marked stand-in for a CTC illustration PDF.

An illustration is a regulated document. When the calculation engine cannot be reached we still
want the rest of the shape to work -- the request is built and validated, a document is filed
against the client, and the adviser can see what was sent -- but the artefact that lands MUST be
impossible to mistake for a quotation. So this renders a page that says PLACEHOLDER in 22pt, states
why, repeats it as a watermark, and carries no projected values of any kind.

The PDF is written by hand rather than through WeasyPrint. WeasyPrint is a declared dependency of
this app but its native stack (Pango/Cairo) is installed only in ``Dockerfile.worker``, so
``import weasyprint`` fails inside the API process. A fallback that itself depends on an optional
native library is not a fallback, and the one moment this module exists for is the moment everything
else has already failed.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.integrations.ctc.xml_builder import IllustrationRequest, PlanStream

# A4, in points.
_W = 595
_H = 842
_LEFT = 42
_RIGHT = _W - 42
_VALUE_X = 232

_REGULAR = b"F1"
_BOLD = b"F2"


def _esc(text: str) -> bytes:
    """A PDF literal string in WinAnsi (== cp1252, so an em-dash and a pound sign survive)."""
    raw = text.encode("cp1252", "replace")
    for pattern, replacement in ((b"\\", b"\\\\"), (b"(", b"\\("), (b")", b"\\)")):
        raw = raw.replace(pattern, replacement)
    return raw


def _n(value: float) -> bytes:
    """A PDF number. Trailing zeros trimmed so the content stream stays readable."""
    return f"{value:g}".encode("ascii")


class _Canvas:
    """The few PDF operators this page needs. Origin is bottom-left, as PDF has it."""

    def __init__(self) -> None:
        self._ops: list[bytes] = []

    def rect(self, x: float, y: float, w: float, h: float, rgb: tuple[float, float, float]) -> None:
        r, g, b = rgb
        self._ops.append(
            b"q " + _n(r) + b" " + _n(g) + b" " + _n(b) + b" rg " + _n(x) + b" " + _n(y) + b" "
            b"" + _n(w) + b" " + _n(h) + b" re f Q"
        )

    def text(
        self,
        x: float,
        y: float,
        value: str,
        *,
        size: float = 9.5,
        bold: bool = False,
        rgb: tuple[float, float, float] = (0.1, 0.1, 0.12),
    ) -> None:
        r, g, b = rgb
        self._ops.append(
            b"BT "
            + _n(r)
            + b" "
            + _n(g)
            + b" "
            + _n(b)
            + b" rg /"
            + (_BOLD if bold else _REGULAR)
            + b" "
            + _n(size)
            + b" Tf 1 0 0 1 "
            + _n(x)
            + b" "
            + _n(y)
            + b" Tm ("
            + _esc(value)
            + b") Tj ET"
        )

    def rotated_text(self, x: float, y: float, value: str, *, size: float) -> None:
        """45 degrees anticlockwise -- the watermark."""
        self._ops.append(
            b"BT 0.9 0.9 0.9 rg /" + _BOLD + b" " + _n(size) + b" Tf "
            b"0.7071 0.7071 -0.7071 0.7071 "
            + _n(x)
            + b" "
            + _n(y)
            + b" Tm ("
            + _esc(value)
            + b") Tj ET"
        )

    def stream(self) -> bytes:
        return b"\n".join(self._ops)


def _wrap(text: str, size: float, width: float) -> list[str]:
    """Greedy wrap. Helvetica has no metrics here, so assume an average glyph of 0.5em -- it
    under-fills a line slightly rather than overflowing the margin, which is the safe direction."""
    limit = max(16, int(width / (size * 0.5)))
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) > limit and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _money(amount: Decimal | float | int | None) -> str:
    if amount is None:
        return "—"
    return f"£{Decimal(str(amount)):,.2f}"


def _stream_label(stream: PlanStream) -> str:
    parts = [stream.contribution_type]
    if stream.net_gross:
        parts.append(stream.net_gross.lower())
    if stream.crystallised:
        parts.append("crystallised")
    return f"{parts[0]} ({', '.join(parts[1:])})" if len(parts) > 1 else parts[0]


def _pdf(page: bytes) -> bytes:
    """Wrap a content stream in the smallest valid single-page PDF."""
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 "
        + _n(_W)
        + b" "
        + _n(_H)
        + b"] /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(page)).encode("ascii") + b" >>\nstream\n" + page + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(index).encode("ascii") + b" 0 obj\n" + body + b"\nendobj\n"

    start_xref = len(out)
    size = len(objects) + 1
    out += b"xref\n0 " + str(size).encode("ascii") + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        b"trailer\n<< /Size "
        + str(size).encode("ascii")
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(start_xref).encode("ascii")
        + b"\n%%EOF\n"
    )
    return bytes(out)


def render_placeholder_pdf(
    request: IllustrationRequest,
    *,
    reason: str,
    engine_errors: list[str] | None = None,
    document_location: str | None = None,
    request_xml: str = "",
    now: datetime,
) -> bytes:
    """Render the stand-in page for ``request``.

    ``reason`` is why no real illustration exists and is printed on the page -- it is not an
    internal detail, it is the point of the document.
    """
    canvas = _Canvas()

    # The band, then the watermark, then the text -- later operators paint over earlier ones.
    canvas.rect(0, _H - 96, _W, 96, (0.62, 0.11, 0.11))
    canvas.text(_LEFT, _H - 52, "PLACEHOLDER ILLUSTRATION", size=23, bold=True, rgb=(1, 1, 1))
    canvas.text(
        _LEFT,
        _H - 74,
        "Not a quotation. No projected values. Do not issue to a client.",
        size=11,
        rgb=(1, 1, 1),
    )
    canvas.rotated_text(120, 210, "PLACEHOLDER", size=62)

    y: float = _H - 130

    def heading(text: str) -> None:
        nonlocal y
        canvas.text(_LEFT, y, text, size=11, bold=True)
        y -= 16

    def paragraph(text: str, *, size: float = 9.5) -> None:
        nonlocal y
        for line in _wrap(text, size, _RIGHT - _LEFT):
            canvas.text(_LEFT, y, line, size=size)
            y -= size + 3.5
        y -= 6

    def row(label: str, value: str) -> None:
        nonlocal y
        canvas.text(_LEFT, y, label, size=9, rgb=(0.42, 0.42, 0.46))
        canvas.text(_VALUE_X, y, value, size=9)
        y -= 14

    heading("Why this is a placeholder")
    paragraph(reason)
    if document_location:
        paragraph(f"The engine named the document “{document_location}”, which we could not fetch.")
    for error in engine_errors or []:
        paragraph(f"Engine message: {error}")

    heading("The request that was built")
    investor = request.investors[0] if request.investors else None
    quotation = request.quotations[0] if request.quotations else None
    if investor is not None:
        name = " ".join(
            part for part in (investor.title, investor.forename, investor.surname) if part
        )
        row("Investor", name or "—")
        row("Date of birth", investor.date_of_birth or "not recorded")
        row("Retirement age", str(investor.exit_age) if investor.exit_age else "not stated")
    if request.client_name:
        row("Client", request.client_name)
    row("Client type", request.client_type or "—")
    if quotation is not None:
        row("Product", f"{quotation.product_type or quotation.product_ref}")
        row("Product reference", quotation.product_ref)
        row("Basis", quotation.quotation_type)
        if quotation.withdrawals is not None:
            row("Drawdown", quotation.withdrawals.drawdown_type)
            row("Withdrawal", _money(quotation.withdrawals.amount))
    row("Effective date", request.effective_date)
    row("Reference", request.output_reference or "—")
    row("Agent", request.agent_name or "not set (CTC_AGENT_NAME)")
    y -= 6

    if quotation is not None and quotation.plan_streams:
        heading("Contributions in the request")
        for stream in quotation.plan_streams:
            row(_stream_label(stream), f"{_money(stream.amount)} · {stream.frequency}")
        y -= 6

    heading("With the integration live")
    paragraph(
        "This request is sent to the CTC calculation engine, which returns the illustration PDF "
        "and files it here in place of this page. Nothing else about the record changes."
    )

    canvas.rect(_LEFT, 74, _RIGHT - _LEFT, 0.6, (0.8, 0.8, 0.82))
    stamp = f"Graphene placeholder · generated {now.strftime('%d %b %Y %H:%M UTC')}"
    if request_xml:
        stamp += f" · CTC1 request {len(request_xml):,} characters, schema-validated"
    canvas.text(_LEFT, 60, stamp, size=8, rgb=(0.42, 0.42, 0.46))
    canvas.text(
        _LEFT,
        48,
        "Produced by Graphene because the calculation engine was unavailable. Not a Morgan Lloyd "
        "document.",
        size=8,
        rgb=(0.42, 0.42, 0.46),
    )

    return _pdf(canvas.stream())
