/**
 * Tiny CSV/row parsing for the inline-dataset authoring form.
 *
 * The naive `line.split(',')` corrupts any value that legitimately contains a comma — a normal UK
 * figure like `£325,000` silently becomes two cells, which then rides into a generated (regulated)
 * document as wrong data. So we parse quote-aware (a value wrapped in double quotes keeps its
 * commas; `""` is a literal quote) and separately report rows whose cell count doesn't match the
 * declared columns, so the mismatch is surfaced instead of silently accepted.
 */

/** Trim a raw segment, then, if it's a quoted value, strip the outer quotes and unescape `""`. */
function parseCell(segment: string): string {
	const t = segment.trim();
	if (t.length >= 2 && t.startsWith('"') && t.endsWith('"')) {
		return t.slice(1, -1).replace(/""/g, '"');
	}
	return t;
}

/** Split one line into cells, respecting double-quoted values (a comma inside quotes is literal;
 *  `""` is an escaped quote). Two phases: split on UNQUOTED commas, then trim + unquote each cell —
 *  so `2025/26, "£325,000"` → `['2025/26', '£325,000']`, surrounding spaces and all. */
export function parseCsvLine(line: string): string[] {
	const segments: string[] = [];
	let cur = '';
	let inQuotes = false;
	for (let i = 0; i < line.length; i++) {
		const ch = line[i];
		if (ch === '"') {
			if (inQuotes && line[i + 1] === '"') {
				cur += '""'; // keep the escape for parseCell to collapse
				i++;
			} else {
				cur += '"';
				inQuotes = !inQuotes;
			}
		} else if (ch === ',' && !inQuotes) {
			segments.push(cur);
			cur = '';
		} else {
			cur += ch;
		}
	}
	segments.push(cur);
	return segments.map(parseCell);
}

export interface InlineTable {
	columns: string[];
	rows: string[][];
	/** Rows whose cell count differs from the column count (1-based line numbers + what was parsed).
	 *  Usually a comma inside an unquoted value that should have been wrapped in double quotes. */
	raggedRows: { line: number; got: number }[];
}

/** Parse the inline-dataset "columns" (one line) + "rows" (one row per line) textareas quote-aware,
 *  flagging any row whose cell count doesn't match the columns. */
export function parseInlineTable(columnsStr: string, rowsStr: string): InlineTable {
	const columns = parseCsvLine(columnsStr).filter(Boolean);
	const lines = rowsStr.split(/\r?\n/).map((l) => l.trim());
	const rows: string[][] = [];
	const raggedRows: { line: number; got: number }[] = [];
	lines.forEach((line, i) => {
		if (!line) return;
		const cells = parseCsvLine(line);
		if (columns.length && cells.length !== columns.length) {
			raggedRows.push({ line: i + 1, got: cells.length });
		}
		rows.push(cells);
	});
	return { columns, rows, raggedRows };
}

/** Serialise one cell for a CSV line — quote it (and escape inner quotes as `""`) only when it
 *  contains a comma, quote or newline, so plain values stay clean and `£325,000` round-trips. */
export function toCsvCell(value: string): string {
	const v = value ?? '';
	return /[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

/** Serialise a row of cells to a single quote-aware CSV line (inverse of `parseCsvLine`). */
export function toCsvLine(cells: string[]): string {
	return cells.map(toCsvCell).join(',');
}

/** Parse a whole CSV file's text into a rectangular {columns, rows}: the first non-empty line is
 *  the header, the rest are data rows padded/truncated to the column count so the grid stays
 *  rectangular (a short row gets blank trailing cells; extra cells are dropped). */
export function parseCsvFile(text: string): { columns: string[]; rows: string[][] } {
	const lines = text.split(/\r?\n/).filter((l) => l.trim() !== '');
	if (lines.length === 0) return { columns: [], rows: [] };
	const columns = parseCsvLine(lines[0]).map((c) => c.trim());
	const width = columns.length;
	const rows = lines.slice(1).map((line) => {
		const cells = parseCsvLine(line);
		if (cells.length < width) return [...cells, ...Array(width - cells.length).fill('')];
		return cells.slice(0, width);
	});
	return { columns, rows };
}
