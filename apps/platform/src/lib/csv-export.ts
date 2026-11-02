/**
 * Writing CSV for the listing exports.
 *
 * Separate from `csv.ts`, which parses the dataset authoring form's own textareas. Same file
 * format, opposite direction, and one hazard that only applies here: an export is opened in a
 * spreadsheet, and every cell in it was typed by a person.
 */

import { escapeCsvCell } from '@graphene/ui';

/**
 * Serialise one value for an export — quote-aware, and neutralising a leading formula character
 * with the spreadsheet's own "this is text" convention.
 *
 * `csv.ts`'s `toCsvCell` deliberately has no such guard: it round-trips values through the
 * authoring form, and nothing outside that form reads them. An export is different in both
 * directions. Every cell is attacker-influenced — a client's name, an account reference, an
 * adviser's note, all free text someone typed into the product — and the file is the one artefact
 * a reader is guaranteed to open in Excel. Without this, exporting the book runs whatever was
 * planted in it: `=HYPERLINK(...)` posting the row to someone else's server, `=cmd|...` on older
 * Excel.
 *
 * The guard itself lives in `@graphene/ui` (`lib/csv.ts`), shared with `apps/admin/src/lib/csv.ts`'s
 * `csvCell` — this wrapper keeps the name `csv-export.test.ts` and this file's own callers pin.
 */
export function exportCell(value: unknown): string {
	return escapeCsvCell(value);
}

/**
 * A whole CSV file: a header row, then one row per record, CRLF endings, leading BOM.
 *
 * The BOM is not decoration. Excel on Windows reads a BOM-less UTF-8 file as the system codepage,
 * so a client called Åsa and every £ sign arrive mangled — in the one place people actually look
 * at this data.
 */
export function csvFile(headers: readonly string[], rows: readonly unknown[][]): string {
	const lines = [
		headers.map(exportCell).join(','),
		...rows.map((row) => row.map(exportCell).join(','))
	];
	// The escape, not a literal BOM character: a zero-width mark sitting in the source is
	// invisible to a reader and trips `no-irregular-whitespace`.
	return `\uFEFF${lines.join('\r\n')}\r\n`;
}

/** The response an export route returns: the right type, a filename, and never cached. */
export function csvResponse(filename: string, body: string): Response {
	return new Response(body, {
		headers: {
			'Content-Type': 'text/csv; charset=utf-8',
			'Content-Disposition': `attachment; filename="${filename}"`,
			// A firm's whole book sitting in a shared cache is exactly what this header prevents.
			'Cache-Control': 'private, no-store'
		}
	});
}

/** Today as `YYYY-MM-DD`, for the filename — so a folder of exports sorts and reads chronologically. */
export function stamp(now = new Date()): string {
	return now.toISOString().slice(0, 10);
}

/** The formats the export menu offers. Anything else falls back to CSV rather than 404ing: the
 *  format is a query param, and a hand-edited one should not lose you the export. */
export type ExportFormat = 'csv' | 'xlsx' | 'pdf';

export function formatFrom(url: URL): ExportFormat {
	const asked = url.searchParams.get('format');
	return asked === 'pdf' ? 'pdf' : asked === 'xlsx' ? 'xlsx' : 'csv';
}

/**
 * What a column HOLDS, so the workbook can format it.
 *
 * Declared per route, never inferred, and `text` is the default — because the inference that looks
 * obvious is the one that corrupts data. This book's transaction references include `000839969` and
 * `000545341`: all digits, and "all digits means number" turns them into 839969 and 545341, which
 * is a reference that matches nothing at the custodian. Only a column that is genuinely a quantity
 * gets to be a number.
 */
export type ExportCellType = 'text' | 'number' | 'money' | 'date';
