/**
 * The printable copy of a terms declaration — a self-contained HTML document, not a portal page.
 *
 * An in-person acceptance is recorded against a SIGNED COPY, and the client can only sign what
 * somebody handed them. This is that hand-out: the declaration's own text (the same snapshot the
 * portal shows the client), a letterhead line, and a signature block. No app shell, no scripts
 * beyond the print call — a print sheet is a document, and the browser's print dialog is the UI.
 */

export interface PrintableTerms {
	/** The declaration's title, e.g. "General declaration". */
	name: string;
	/** Already-sanitised HTML from the API. Trusted as such: this function does not sanitise. */
	content: string;
	/** Who is signing, when the item is attached to a client. */
	clientName: string | null;
	/** The firm's name for the letterhead line, when known. */
	firmName: string | null;
	/** The date printed on the sheet, so it reads as a dated document. */
	printedOn: Date;
}

/** Escape text for an HTML text node or a double-quoted attribute. */
export function escapeHtml(s: string): string {
	return s
		.replaceAll('&', '&amp;')
		.replaceAll('<', '&lt;')
		.replaceAll('>', '&gt;')
		.replaceAll('"', '&quot;')
		.replaceAll("'", '&#39;');
}

const STYLE = `
	@page { size: A4; margin: 20mm 18mm; }
	:root { color-scheme: light; }
	html, body { margin: 0; padding: 0; background: #fff; color: #111; }
	body {
		font: 11pt/1.55 Georgia, 'Times New Roman', serif;
		max-width: 174mm; margin: 0 auto; padding: 24px 16px 48px;
	}
	header { border-bottom: 1px solid #999; padding-bottom: 10px; margin-bottom: 22px; }
	header .firm { font: 600 10pt/1.3 system-ui, -apple-system, 'Segoe UI', sans-serif;
		letter-spacing: .04em; text-transform: uppercase; color: #444; }
	header h1 { font-size: 18pt; margin: 6px 0 2px; line-height: 1.25; }
	header .meta { font: 10pt/1.4 system-ui, -apple-system, 'Segoe UI', sans-serif; color: #444; }
	main h1, main h2, main h3 { line-height: 1.3; margin: 1.2em 0 .4em; page-break-after: avoid; }
	main h1 { font-size: 14pt; } main h2 { font-size: 13pt; } main h3 { font-size: 12pt; }
	main p, main li { orphans: 3; widows: 3; }
	main ul, main ol { padding-left: 1.4em; }
	main a { color: inherit; text-decoration: underline; }
	main img { max-width: 100%; }
	.signature { margin-top: 36px; page-break-inside: avoid; }
	.signature p.lead { margin: 0 0 18px; }
	.signature .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 28px 32px; }
	.signature .line { border-bottom: 1px solid #111; height: 34px; }
	.signature .label { font: 9pt/1.3 system-ui, -apple-system, 'Segoe UI', sans-serif;
		color: #444; margin-top: 6px; }
	.toolbar { position: sticky; top: 0; display: flex; justify-content: flex-end; gap: 8px;
		padding: 10px 0; background: #fff; }
	.toolbar button { font: 600 10.5pt system-ui, -apple-system, 'Segoe UI', sans-serif;
		padding: 7px 14px; border: 1px solid #333; border-radius: 6px; background: #fff;
		color: #111; cursor: pointer; }
	.toolbar button:hover { background: #f3f3f3; }
	@media print { .toolbar { display: none; } body { padding: 0; max-width: none; } }
`;

/** The whole document. `content` is inserted as HTML; every other value is escaped. */
export function printableTerms(input: PrintableTerms): string {
	const title = input.name.trim() || 'Declaration';
	const dated = input.printedOn.toLocaleDateString('en-GB', {
		day: 'numeric',
		month: 'long',
		year: 'numeric'
	});
	const meta = [
		input.clientName ? `Prepared for ${escapeHtml(input.clientName)}` : null,
		`Printed ${escapeHtml(dated)}`
	]
		.filter(Boolean)
		.join(' · ');
	const firm = input.firmName ? `<p class="firm">${escapeHtml(input.firmName)}</p>` : '';
	return `<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex">
<title>${escapeHtml(title)}</title>
<style>${STYLE}</style>
</head>
<body>
<div class="toolbar"><button type="button" id="print">Print</button></div>
<header>
${firm}
<h1>${escapeHtml(title)}</h1>
<p class="meta">${meta}</p>
</header>
<main>
${input.content}
</main>
<section class="signature" aria-label="Signature">
<p class="lead">I confirm that I have read and agree to the ${escapeHtml(title)} above.</p>
<div class="grid">
<div><div class="line"></div><p class="label">Signature</p></div>
<div><div class="line"></div><p class="label">Date</p></div>
<div><div class="line">${input.clientName ? `<span style="display:inline-block;padding-top:10px">${escapeHtml(input.clientName)}</span>` : ''}</div><p class="label">Full name</p></div>
<div><div class="line"></div><p class="label">Witnessed by (adviser)</p></div>
</div>
</section>
<script>
document.getElementById('print').addEventListener('click', function () { window.print(); });
window.addEventListener('load', function () { setTimeout(function () { window.print(); }, 150); });
</script>
</body>
</html>
`;
}
