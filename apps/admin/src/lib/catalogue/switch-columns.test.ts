/**
 * A DataTable column whose cell is a Switch must be CENTRED, never right-aligned.
 *
 * `align: 'right'` sets `justify-content: flex-end` on the cell. That pins the control to the
 * right edge, so the row can never give the column its space back — the table then scrolls
 * horizontally with the panel half empty. This has been fixed twice: once on the
 * transfer-providers table (ca4bd7b, whose message records that the fix before it "silently
 * missed" because a string replacement did not match the indentation), and again on the products
 * table when it was rebuilt as a DataTable and reintroduced the same `align: 'right'`.
 *
 * The working shape is `headerClass`/`cellClass` of `w-24 text-center` plus `flex justify-center`
 * on the control, which is what the assertion below describes. It is a source scan because the
 * defect is a CSS interaction: nothing about `align: 'right'` is a type error, and the table still
 * renders — it just overflows.
 */
import { describe, expect, it } from 'vitest';
import { globSync, readFileSync } from 'node:fs';

/** The catalogue tables that carry a per-row Switch. */
const SOURCES = globSync('src/lib/catalogue/*.svelte').filter((path) =>
	readFileSync(path, 'utf8').includes('<Switch')
);

/** A column literal with its `//` comments stripped.
 *
 * Necessary, not fussy: the fixed column carries a comment naming the very mistake it avoids
 * (`NOT align: 'right'` …), and a scan over the raw text flags that as the defect. Matching on a
 * comment is how a guard starts reporting the explanation instead of the code. */
function withoutComments(column: string): string {
	return column.replace(/\/\/[^\n]*/g, '');
}

/** One column definition object, as written in a `DataTableColumn<T>[]` literal. */
function columnLiterals(source: string): string[] {
	// Column defs are either `{ key: 'x', … }` on one line or a multi-line object; both start at
	// `{ key:` or `{\n\t\t\tkey:` and end at the matching depth-0 brace.
	const out: string[] = [];
	const start = /\{\s*\n?\s*key:/g;
	for (const match of source.matchAll(start)) {
		let depth = 0;
		for (let i = match.index; i < source.length; i += 1) {
			if (source[i] === '{') depth += 1;
			else if (source[i] === '}') {
				depth -= 1;
				if (depth === 0) {
					out.push(source.slice(match.index, i + 1));
					break;
				}
			}
		}
	}
	return out;
}

describe('a switch column in the catalogue tables', () => {
	it('has tables to scan', () => {
		// Otherwise the assertions below pass vacuously if the glob or the `<Switch` probe breaks.
		expect(SOURCES.length).toBeGreaterThanOrEqual(2);
	});

	it('is centred with a fixed width, never right-aligned', () => {
		const offenders: string[] = [];
		for (const path of SOURCES) {
			const source = readFileSync(path, 'utf8');
			for (const raw of columnLiterals(source)) {
				const column = withoutComments(raw);
				// The switch column is the one keyed on the enablement flag.
				if (!/key:\s*'(enabled|offered)'/.test(column)) continue;
				if (/align:\s*'right'/.test(column)) {
					offenders.push(`${path}: align: 'right' on a switch column`);
				}
				if (!/cellClass:\s*'[^']*text-center/.test(column)) {
					offenders.push(`${path}: switch column has no centring cellClass`);
				}
			}
		}
		expect(offenders).toEqual([]);
	});

	it('does not pin the control to an edge', () => {
		const offenders = SOURCES.filter((path) => {
			const source = readFileSync(path, 'utf8');
			// `justify-end` is the class form of the same mistake.
			return /<form[^>]*class="[^"]*justify-end/.test(source);
		});
		expect(offenders).toEqual([]);
	});
});
