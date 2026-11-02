import { jsPDF } from 'jspdf';
import { autoTable } from 'jspdf-autotable';
import regularUrl from './fonts/LiberationSans-Regular.ttf?url';
import boldUrl from './fonts/LiberationSans-Bold.ttf?url';

/**
 * A listing's PDF, drawn in the BROWSER from the table as the screen shows it.
 *
 * The export route sends the rows already cut to the table's columns, words and order
 * (`export-response.ts` in the platform app); this only lays them out. It used to be rendered by
 * the document worker (WeasyPrint), which took about 40 ms a row and gave up after a minute — so
 * a book past ~1,500 rows never arrived at all.
 *
 * Imported lazily by the export menu, so jsPDF and the fonts load only when someone asks for a PDF.
 */

/** What the export route answers a PDF request with. */
export interface ExportTable {
	/** With the `.pdf`. */
	filename: string;
	title: string;
	/** What the rows are, and what they were filtered to. */
	subtitle: string;
	headers: string[];
	rows: string[][];
}

/** Regular and bold TrueType files. jsPDF's built-in fonts are Latin-1 only, so a name like
 *  `Łukasz` or a `—` in an empty cell would print as noise; Liberation Sans covers both. */
export interface PdfFonts {
	regular: ArrayBuffer;
	bold: ArrayBuffer;
}

const FONT = 'LiberationSans';

async function bundledFonts(): Promise<PdfFonts> {
	const load = async (url: string) => (await fetch(url)).arrayBuffer();
	const [regular, bold] = await Promise.all([load(regularUrl), load(boldUrl)]);
	return { regular, bold };
}

const HEAD_SIZE = 7;
const BODY_SIZE = 7.5;
/** A cell's left + right padding, mm. */
const PAD = 3;
/** A word up to this wide (an amount, a phone number, a status) stays on one line, mm. */
const WHOLE = 30;
/** A column never claims more than this for one long value — a note wraps instead, mm. */
const WIDEST = 70;

/**
 * Column widths, mm, from the text itself — measured in the real font.
 *
 * Every column first gets its longest word, so `£1,234,567.89` and `Active` are never split while
 * there is room; the width left over goes to the columns with longer text. The table's own auto
 * layout did the opposite: it gave an email column half the page and broke the amounts beside it.
 */
function columnWidths(doc: jsPDF, font: string, table: ExportTable, available: number): number[] {
	const need = table.headers.map(() => 0);
	const want = table.headers.map(() => 0);
	const words = (text: string) => text.split(/\s+/).filter(Boolean);
	const take = (i: number, text: string) => {
		for (const line of text.split('\n')) {
			want[i] = Math.max(want[i] ?? 0, doc.getTextWidth(line));
			for (const word of words(line)) need[i] = Math.max(need[i] ?? 0, doc.getTextWidth(word));
		}
	};
	doc.setFont(font, 'bold').setFontSize(HEAD_SIZE);
	table.headers.forEach((header, i) => take(i, header));
	doc.setFont(font, 'normal').setFontSize(BODY_SIZE);
	for (const row of table.rows) row.forEach((cell, i) => take(i, cell));

	const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
	const least = need.map((n) => Math.min(n, WHOLE) + PAD);
	const most = want.map((w, i) => Math.max(least[i] ?? 0, Math.min(w, WIDEST) + PAD));
	const spare = available - sum(least);
	const growth = sum(most) - sum(least);
	// Too narrow even for the words, or nothing wants more: share the page by what each needs.
	if (spare <= 0 || growth <= 0) return least.map((l) => (l * available) / sum(least));
	// Room for every column at its widest: stretch them all to the page.
	if (spare >= growth) return most.map((m) => (m * available) / sum(most));
	return least.map((l, i) => l + (spare * ((most[i] ?? l) - l)) / growth);
}

function base64(bytes: ArrayBuffer): string {
	const view = new Uint8Array(bytes);
	let text = '';
	// In slices: one `fromCharCode` call over 400 KB overflows the argument stack.
	for (let i = 0; i < view.length; i += 0x8000) {
		text += String.fromCharCode(...view.subarray(i, i + 0x8000));
	}
	return btoa(text);
}

/**
 * Draw the table. `fonts: null` keeps jsPDF's own Helvetica — for tests, which have no asset
 * server to fetch the bundled files from.
 */
export async function tablePdf(
	table: ExportTable,
	{ fonts }: { fonts?: PdfFonts | null } = {}
): Promise<Blob> {
	// A table is wider than it is tall; past six columns it needs the long edge.
	const doc = new jsPDF({
		orientation: table.headers.length > 6 ? 'landscape' : 'portrait',
		unit: 'mm',
		format: 'a4',
		// Uncompressed, 2,000 rows came to 9 MB.
		compress: true
	});
	const loaded = fonts === undefined ? await bundledFonts() : fonts;
	let font = 'helvetica';
	if (loaded) {
		doc.addFileToVFS(`${FONT}-Regular.ttf`, base64(loaded.regular));
		doc.addFileToVFS(`${FONT}-Bold.ttf`, base64(loaded.bold));
		doc.addFont(`${FONT}-Regular.ttf`, FONT, 'normal');
		doc.addFont(`${FONT}-Bold.ttf`, FONT, 'bold');
		font = FONT;
	}

	const margin = 10;
	doc.setFont(font, 'bold').setFontSize(13).setTextColor(17);
	doc.text(table.title, margin, 15);
	doc.setFont(font, 'normal').setFontSize(8).setTextColor(85);
	if (table.subtitle) doc.text(table.subtitle, margin, 20);

	const widths = columnWidths(doc, font, table, doc.internal.pageSize.getWidth() - 2 * margin);
	autoTable(doc, {
		head: [table.headers],
		body: table.rows,
		columnStyles: Object.fromEntries(widths.map((cellWidth, i) => [i, { cellWidth }])),
		startY: 24,
		margin: { top: 12, right: margin, bottom: 14, left: margin },
		theme: 'plain',
		// The header repeats on every page, and a row is never split across two.
		showHead: 'everyPage',
		rowPageBreak: 'avoid',
		styles: {
			font,
			fontSize: BODY_SIZE,
			textColor: 17,
			cellPadding: { top: 1.2, right: 1.5, bottom: 1.2, left: 1.5 },
			// Long text wraps inside its cell — never clipped, never cut short.
			overflow: 'linebreak',
			valign: 'top'
		},
		headStyles: {
			fontStyle: 'bold',
			fontSize: HEAD_SIZE,
			lineColor: 51,
			lineWidth: { bottom: 0.3 }
		},
		bodyStyles: { lineColor: 221, lineWidth: { bottom: 0.1 } }
	});

	// Numbered once the count is known. Not `putTotalPages`: its placeholder is never found in text
	// an embedded font has encoded.
	const pages = doc.getNumberOfPages();
	const { width, height } = doc.internal.pageSize;
	doc.setFont(font, 'normal').setFontSize(7).setTextColor(119);
	for (let page = 1; page <= pages; page++) {
		doc.setPage(page);
		doc.text(`Page ${page} of ${pages}`, width - margin, height - 7, { align: 'right' });
	}
	return doc.output('blob');
}
