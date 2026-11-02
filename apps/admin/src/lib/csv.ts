/**
 * Minimal RFC 4180 CSV writing, for operator exports (the audit trail).
 *
 * Worth its own module because audit `detail` payloads routinely contain commas, quotes and
 * newlines — the substance of the evidence — and a naive `join(',')` silently corrupts the file a
 * compliance request is built on.
 */

import { escapeCsvCell } from '@graphene/ui';

/**
 * Escape one value: objects become JSON, and anything with a delimiter/quote/newline is quoted.
 *
 * A leading `=`/`+`/`-`/`@` is neutralised with a `'` prefix (the spreadsheet convention for
 * "this is text"). Audit `detail` is attacker-influenced — a firm name, an email, a document title
 * someone typed — so without this, opening the compliance export executes whatever was planted in
 * it (CSV injection: `=HYPERLINK(...)` exfiltrating the row, `=cmd|...` on older Excel). The export
 * is the one artefact an operator is guaranteed to open in Excel.
 *
 * The guard itself lives in `@graphene/ui` (`lib/csv.ts`), shared with
 * `apps/platform/src/lib/csv-export.ts`'s `exportCell` — this wrapper keeps the name this
 * module's own callers and tests already use.
 */
export function csvCell(value: unknown): string {
	return escapeCsvCell(value);
}

/** A CSV document: a header row from `columns`, then one row per record. CRLF line endings. */
export function csvDocument<T extends Record<string, unknown>>(
	columns: readonly (keyof T & string)[],
	rows: readonly T[]
): string {
	const lines = [
		columns.join(','),
		...rows.map((row) => columns.map((c) => csvCell(row[c])).join(','))
	];
	return `${lines.join('\r\n')}\r\n`;
}
