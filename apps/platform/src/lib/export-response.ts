import { error, json } from '@sveltejs/kit';
import { formatMoney, formatSignedMoney } from '@graphene/ui';
import type { ExportTable } from '@graphene/ui/book/listing';
import { apiUrl, jsonHeaders } from '$lib/api';
import { csvFile, csvResponse, type ExportCellType, type ExportFormat } from '$lib/csv-export';
// Type-only, so it is erased at build time and the writer itself still loads lazily below.
import type { SheetData } from 'write-excel-file/node';

/**
 * One export, three containers.
 *
 * Every route builds the SAME headers and rows and hands them here, so the CSV, the workbook and
 * the PDF of a listing can never disagree about what is in it — which they would the moment each
 * format grew its own column list. That already happened once: the account ledger gained Units,
 * ISIN, Ccy and Balance and its export did not, so the file carried a Method column the table has
 * never shown.
 *
 * The PDF is drawn in the BROWSER (`@graphene/ui`'s `tablePdf`) from what this sends: the table
 * as the screen shows it. It used to be rendered by the document worker (WeasyPrint), which took
 * about 40 ms a row and gave up after a minute, so a large book never arrived at all.
 */

export interface ExportSpec {
	/** Which listing this is (`clients`, `account-transactions`…), for the audit record. */
	listing: string;
	/** Without the extension — this adds the one that matches the format. */
	filename: string;
	title: string;
	/** States the filters, so the file says what it is a list OF. */
	subtitle?: string;
	headers: readonly string[];
	/**
	 * What each column holds, positionally against `headers`. Omitted ⇒ every column is text.
	 *
	 * Only the workbook reads this. CSV and PDF are text formats and the values arrive already
	 * formatted; a spreadsheet is the only one that can hold a real date or a real number, and the
	 * only one where getting it wrong is silent.
	 */
	types?: readonly ExportCellType[];
	rows: readonly unknown[][];
	/**
	 * Headers the table does NOT show. The workbook and the CSV carry them — a spreadsheet is
	 * where someone needs the email, the phone, the reference — and the PDF leaves them out, so
	 * the PDF reads as the table on screen: its columns, its names, its order. `headers` is
	 * therefore in the TABLE's order, with these interleaved.
	 */
	sheetOnly?: readonly string[];
}

/** The value as the text formats want it. */
const asText = (cell: unknown): string => (cell === null || cell === undefined ? '' : String(cell));

/**
 * A cell that reads differently in a spreadsheet than on screen: `value` is what a spreadsheet
 * can sort and add up (`1234567.89`), `text` is what the table cell shows (`£1,234,567.89`). The
 * workbook and the CSV take the value, the PDF the text.
 */
export class Shown {
	constructor(
		readonly value: unknown,
		readonly text: string
	) {}
}
export const shown = (value: unknown, text: string) => new Shown(value, text);

/** A figure as the table's `<Money>` cell shows it, with the raw number for a spreadsheet. */
export function moneyCell(
	amount: number | null | undefined,
	currency: string | null | undefined,
	{ signed = false } = {}
): Shown {
	const ccy = currency || 'GBP';
	if (amount == null || !Number.isFinite(amount)) return shown(amount ?? null, '—');
	return shown(amount, signed ? formatSignedMoney(amount, ccy) : formatMoney(amount, ccy));
}

const sheetValue = (cell: unknown) => (cell instanceof Shown ? cell.value : cell);
const screenText = (cell: unknown) => (cell instanceof Shown ? cell.text : asText(cell));

/** The spec as the PDF wants it: only the columns the table shows, as the table shows them. */
export function onScreen(spec: ExportSpec): { headers: string[]; rows: string[][] } {
	const hidden = new Set(spec.sheetOnly ?? []);
	for (const name of hidden) {
		// A misspelt name would silently leave the column in the PDF.
		if (!spec.headers.includes(name)) throw new Error(`sheetOnly names no header: ${name}`);
	}
	const keep = spec.headers.flatMap((h, i) => (hidden.has(h) ? [] : [i]));
	return {
		headers: keep.map((i) => spec.headers[i]),
		rows: spec.rows.map((row) => keep.map((i) => screenText(row[i])))
	};
}

/** Past this a PDF stops being something anyone reads; Excel and CSV have no such limit. */
export const PDF_MAX_ROWS = 5_000;

/** The exporting request's own `fetch` and token, for the audit record. */
export interface ExportRecorder {
	fetch: typeof fetch;
	accessToken: string | null | undefined;
}

export async function exportResponse(
	format: ExportFormat,
	spec: ExportSpec,
	recorder: ExportRecorder
): Promise<Response> {
	// Built first: a refused export (a PDF over the cap) sent nothing, so it records nothing.
	const response = await build(format, spec);
	await recordExport(recorder, { listing: spec.listing, format, rows: spec.rows.length });
	return response;
}

/**
 * Every export is recorded: a listing leaving the platform as a file is data egress. The API
 * writes it to the audit trail as `export.listing_exported`, with the format in its details.
 * Best-effort, as the audit floor itself is: a failed record is logged and the file still goes.
 */
async function recordExport(
	recorder: ExportRecorder,
	body: { listing: string; format: ExportFormat; rows: number }
) {
	try {
		const res = await recorder.fetch(`${apiUrl()}/exports/record`, {
			method: 'POST',
			headers: jsonHeaders(recorder.accessToken ?? null),
			body: JSON.stringify(body)
		});
		if (!res.ok)
			console.error(`export record failed: ${body.listing} ${body.format} → ${res.status}`);
	} catch (err) {
		console.error(`export record errored: ${body.listing} ${body.format}`, err);
	}
}

async function build(format: ExportFormat, spec: ExportSpec): Promise<Response> {
	if (format !== 'pdf') {
		const sheet = { ...spec, rows: spec.rows.map((row) => row.map(sheetValue)) };
		if (format === 'csv') {
			return csvResponse(`${spec.filename}.csv`, csvFile(sheet.headers, sheet.rows));
		}
		return workbook(sheet);
	}

	const table = onScreen(spec);
	if (table.rows.length > PDF_MAX_ROWS) {
		// Refused, not truncated: a PDF that quietly stops at the cap looks complete.
		error(
			422,
			`Too many rows for a PDF (${table.rows.length.toLocaleString()}). Export as Excel or CSV instead.`
		);
	}
	const body: ExportTable = {
		filename: `${spec.filename}.pdf`,
		title: spec.title,
		subtitle: spec.subtitle ?? '',
		...table
	};
	return json(body, { headers: { 'Cache-Control': 'private, no-store' } });
}

/** Excel's own number formats. UK date order, money to the penny with thousands separators. */
const FORMATS: Record<Exclude<ExportCellType, 'text'>, string> = {
	date: 'dd/mm/yyyy',
	money: '#,##0.00',
	number: '#,##0.####'
};

/**
 * A title Excel will accept as a worksheet name.
 *
 * `write-excel-file` THROWS on `[ ] / \ : * ?`, and this name is built from a client or account
 * name — a book that contains `MLSIPP/01417` and `Smith / Jones Trust` hits it immediately, and the
 * export 500s rather than degrading. Excel also refuses a leading or trailing apostrophe, and a
 * blank name. 31 characters is its hard limit.
 *
 * The illegal characters become a space rather than being deleted, so `ISA/GIA` stays two words.
 */
export function sheetName(title: string): string {
	const cleaned = title
		.replace(/[[\]/\\:*?]/g, ' ')
		.replace(/\s+/g, ' ')
		.trim()
		.slice(0, 31)
		// The slice can expose a trailing apostrophe that was interior before it.
		.replace(/^'+|'+$/g, '')
		.trim();
	return cleaned || 'Export';
}

/** `2026-08-20` → a UTC date, so a negative timezone offset cannot walk it back a day. */
function parseDate(value: unknown): Date | null {
	const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value ?? ''));
	return m ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])) : null;
}

/**
 * The listing as a real workbook.
 *
 * This is the format people actually open, and it is the one that can arrive finished: a frozen
 * header so row 400 still has column names, an autofilter, column widths, dates that sort as dates
 * and money to the penny. CSV can express none of that — it has no formatting layer at all, which
 * is why a 583-row ledger opens there with `########` dates and no header in view.
 */
async function workbook(spec: ExportSpec): Promise<Response> {
	// Imported lazily and only on this branch: the writer pulls in a zip library, and a CSV or PDF
	// export should not pay for it.
	const { default: writeXlsxFile } = await import('write-excel-file/node');

	const typeAt = (i: number): ExportCellType => spec.types?.[i] ?? 'text';

	// Cast to the library's own `SheetData` at the call: the header cells and the body cells are
	// legitimately different shapes, and left as a bare union TypeScript resolves the call to the
	// OBJECTS overload — which wants a `cell` renderer per column and rejects plain widths.
	const header = spec.headers.map((h) => ({
		value: h,
		fontWeight: 'bold' as const,
		// A rule under the header, so the frozen row reads as a header rather than as row one.
		borderBottomStyle: 'thin' as const
	}));

	const body = spec.rows.map((row) =>
		spec.headers.map((_, i) => {
			const raw = row[i];
			if (raw === null || raw === undefined || raw === '') return { value: null };
			const type = typeAt(i);
			if (type === 'date') {
				const at = parseDate(raw);
				// Unparseable stays TEXT rather than becoming a wrong date or an empty cell.
				return at
					? { type: Date, value: at, format: FORMATS.date }
					: { type: String, value: String(raw) };
			}
			if (type === 'money' || type === 'number') {
				const n = Number(raw);
				return Number.isFinite(n)
					? { type: Number, value: n, format: FORMATS[type] }
					: { type: String, value: String(raw) };
			}
			return { type: String, value: String(raw) };
		})
	);

	// Width from the widest thing in the column, clamped: narrow enough that fourteen columns still
	// fit on a screen, wide enough that a date is never `########` — the whole complaint that got
	// this format built.
	const widths = spec.headers.map((h, i) => {
		const longest = spec.rows.reduce((max, row) => Math.max(max, asText(row[i]).length), h.length);
		const type = typeAt(i);
		const floor = type === 'date' ? 12 : type === 'money' ? 14 : 10;
		return { width: Math.min(44, Math.max(floor, longest + 2)) };
	});

	const handle = await writeXlsxFile([header, ...body] as SheetData, {
		columns: widths,
		// The header stays put and gains filter dropdowns — on a 583-row ledger that is the
		// difference between a table and a wall of numbers.
		stickyRowsCount: 1,
		sheet: sheetName(spec.title)
	});

	return new Response(new Uint8Array(await handle.toBuffer()), {
		headers: {
			'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
			'Content-Disposition': `attachment; filename="${spec.filename}.xlsx"`,
			'Cache-Control': 'private, no-store'
		}
	});
}
