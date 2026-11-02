/**
 * The CSV-injection guard, shared.
 *
 * Was two identical copies — `apps/platform/src/lib/csv-export.ts` and `apps/admin/src/lib/csv.ts`
 * — one per app because there was no shared runtime module for the two SvelteKit servers and
 * inventing one for a dozen lines was judged the larger change. Both apps depend on `@graphene/ui`
 * regardless, so that reasoning no longer holds; each app's own module now wraps this one under
 * its existing exported name, so nothing downstream (including each app's own pinning test)
 * changes.
 */

/**
 * Characters that make a spreadsheet treat a cell as a FORMULA rather than text.
 *
 * `=`, `+`, `-` and `@` start a formula in Excel/Sheets/LibreOffice; tab and carriage return are
 * included because either can shift a payload past a naive check. Quoting does NOT help — the
 * spreadsheet strips the quotes before it parses.
 */
export const CSV_FORMULA_LEAD = /^[=+\-@\t\r]/;

/**
 * Escape one value for a CSV export: objects become JSON, a leading formula character is
 * neutralised with the spreadsheet's own "this is text" convention (`'` prefix), and anything with
 * a delimiter/quote/newline is quoted.
 *
 * Every cell in an export is typically attacker-influenced — a client's name, an account
 * reference, an adviser's note, a firm name, an audit `detail` payload — and the file is the one
 * artefact a reader is guaranteed to open in Excel. Without this, opening the export runs whatever
 * was planted in it: `=HYPERLINK(...)` posting the row to someone else's server, `=cmd|...` on
 * older Excel.
 */
export function csvCell(value: unknown): string {
	if (value === null || value === undefined) return '';
	const raw = typeof value === 'object' ? JSON.stringify(value) : String(value);
	const text = CSV_FORMULA_LEAD.test(raw) ? `'${raw}` : raw;
	return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}
