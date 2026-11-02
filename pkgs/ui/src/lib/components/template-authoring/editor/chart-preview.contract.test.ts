/**
 * Cross-language contract: the editor's chart preview must draw with the SAME colours the render
 * worker bakes into the PDF.
 *
 * Why this test exists. The literals in chart-preview.ts look like exactly the sort of hardcoded
 * colour a token sweep should remove, and they are not — a chart is document content handed to
 * WeasyPrint, which has no app stylesheet and no colour scheme, so a `var(--brand)` would resolve to
 * nothing and a scheme-aware chart would misrepresent the PDF the adviser is about to send. What
 * they DO need is to stay equal to their Python counterparts, and until now that was guaranteed only
 * by a comment on each side saying "kept in sync". This asserts it, so a well-meaning edit to either
 * file fails here instead of silently shipping an editor preview that lies about the output.
 *
 * Reads charts.py as text rather than importing it (no Python in the vitest run) — a narrow parse of
 * two well-known literal blocks, which is enough to catch drift.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { DEFAULT_PALETTE, RENDER_CHROME, VALUE_FORMATS } from './chart-preview';

const CHARTS_PY = fileURLToPath(
	new URL('../../../../../../../apps/api/app/render/charts.py', import.meta.url)
);
const PREVIEW_TS = fileURLToPath(new URL('./chart-preview.ts', import.meta.url));

function pythonSource(): string {
	try {
		return readFileSync(CHARTS_PY, 'utf8');
	} catch (e) {
		throw new Error(
			`Could not read ${CHARTS_PY} — if app/render/charts.py moved, update this contract test ` +
				`rather than deleting it; the palettes still have to match. (${(e as Error).message})`
		);
	}
}

/** `DEFAULT_PALETTE = [ "<hex>", ... ]` → the hex strings, in order. */
function parsePyPalette(src: string): string[] {
	const block = /^DEFAULT_PALETTE\s*=\s*\[([^\]]*)\]/m.exec(src);
	expect(block, 'DEFAULT_PALETTE list not found in charts.py').not.toBeNull();
	return [...block![1].matchAll(/"(#[0-9a-fA-F]{6})"/g)].map((m) => m[1]);
}

/** `_NAME = "<hex>"` → { _NAME: <hex> }. */
function parsePyScalars(src: string): Record<string, string> {
	const out: Record<string, string> = {};
	// `none` as well as a hex: the card's paint is transparent by default, and a hex-only pattern
	// would read it as ABSENT — which passes the drift check by not checking anything.
	for (const m of src.matchAll(/^(_[A-Z_]+)\s*=\s*"(#[0-9a-fA-F]{3,8}|none)"/gm)) out[m[1]] = m[2];
	return out;
}

describe('chart-preview ↔ charts.py render contract', () => {
	it('the series palette matches the worker, element for element and in order', () => {
		// Order matters: series N takes palette[N % len], so a reordering silently recolours
		// every existing chart in every already-authored template.
		expect(DEFAULT_PALETTE).toEqual(parsePyPalette(pythonSource()));
	});

	it('the palette is a real spread, not a placeholder', () => {
		expect(DEFAULT_PALETTE.length).toBeGreaterThanOrEqual(6);
		expect(new Set(DEFAULT_PALETTE).size, 'duplicate colours make series indistinguishable').toBe(
			DEFAULT_PALETTE.length
		);
		for (const c of DEFAULT_PALETTE) expect(c).toMatch(/^#[0-9a-f]{6}$/);
	});

	it('grid / axis / text / card chrome match the worker', () => {
		const py = parsePyScalars(pythonSource());
		for (const [name, ts] of Object.entries(RENDER_CHROME)) {
			expect(py[name], `charts.py no longer defines ${name}`).toBeDefined();
			expect(ts, `${name} drifted between chart-preview.ts and charts.py`).toBe(py[name]);
		}
	});

	it('the default font stack and base size match the worker', () => {
		// A chart's typography is authored once and drawn twice. If the preview's default face or its
		// scaling anchor differs from the worker's, an untouched chart is a different picture in the
		// editor from the one in the PDF — and nothing about it looks wrong in either place alone.
		const py = pythonSource();
		const ts = readFileSync(PREVIEW_TS, 'utf8');
		const pyFamily = /^_FONT_FAMILY\s*=\s*"([^"]+)"/m.exec(py);
		const tsFamily = /^const FONT_FAMILY\s*=\s*'([^']+)'/m.exec(ts);
		expect(pyFamily, '_FONT_FAMILY not found in charts.py').not.toBeNull();
		expect(tsFamily, 'FONT_FAMILY not found in chart-preview.ts').not.toBeNull();
		expect(tsFamily![1]).toBe(pyFamily![1]);

		const pyBase = /^_BASE_FONT\s*=\s*([\d.]+)/m.exec(py);
		const tsBase = /^const BASE_FONT\s*=\s*([\d.]+)/m.exec(ts);
		expect(pyBase, '_BASE_FONT not found in charts.py').not.toBeNull();
		expect(tsBase, 'BASE_FONT not found in chart-preview.ts').not.toBeNull();
		expect(Number(tsBase![1])).toBe(Number(pyBase![1]));
	});

	it('both sides scale the same set of canonical text sizes', () => {
		// Every font-size in either renderer is written as `_fs(…, N)` / `fs(…, N)`, where N is the
		// size at the default base. A title that scales from 13 on one side and 12 on the other only
		// diverges once an author touches the size control, which is exactly the kind of drift a
		// rendered comparison at the default would miss.
		//
		// The DISTINCT sizes, not their count: the preview legitimately draws one thing the PDF does
		// not — a note saying how many zero/negative slices a pie dropped — so the two files call the
		// scaler a different number of times. What neither may do is introduce a size the other has
		// never heard of.
		const sizes = (src: string, call: RegExp): number[] =>
			[...new Set([...src.matchAll(call)].map((m) => Number(m[1])))].sort((a, b) => a - b);
		const py = sizes(pythonSource(), /_fs\(\w+,\s*([\d.]+)\)/g);
		const ts = sizes(readFileSync(PREVIEW_TS, 'utf8'), /\bfs\(\w+,\s*([\d.]+)\)/g);
		expect(py.length, 'no scaled sizes found in charts.py').toBeGreaterThan(2);
		expect(ts.length, 'no scaled sizes found in chart-preview.ts').toBeGreaterThan(2);
		expect(ts).toEqual(py);
	});

	it('both sides step the per-type shape controls off the same numbers', () => {
		// `line_style`, `bar_width` and `ring_width` are NAMED STEPS, and the name is only meaningful
		// through the number behind it. If the preview dashes at `7 5` and the worker at `5 3`, or a
		// wide bar is 0.95 in one file and 0.9 in the other, every chart carrying the setting is drawn
		// two different ways and the author is looking at the wrong one.
		const table = (src: string, name: string, re: RegExp): string[] => {
			const m = re.exec(src);
			expect(m, `${name} table not found`).not.toBeNull();
			// The VALUES in declared order — the first entry is the fallback on both sides, so order
			// carries meaning as much as membership does. Anchored on the COLON, because Python quotes
			// its keys and TypeScript does not: a bare string match read `solid` as a value on one
			// side and not the other, and reported drift between two files that agreed.
			return [...m![1].matchAll(/:\s*(?:["']([^"']*)["']|([\d.]+))/g)].map((v) => v[1] ?? v[2]);
		};
		const py = pythonSource();
		const ts = readFileSync(PREVIEW_TS, 'utf8');
		// String.raw, not a plain template literal: inside one `\s` is an unrecognised escape and
		// evaluates to a bare `s`, so the pattern matched nothing — a test that passes by failing to
		// look at either file.
		const pyRx = (n: string) => new RegExp('^' + n + String.raw`\s*=\s*\{([^}]*)\}`, 'm');
		const tsRx = (n: string) => new RegExp('^const ' + n + String.raw`[^=]*=\s*\{([^}]*)\}`, 'm');
		for (const [pyName, tsName] of [
			['_LINE_DASH', 'LINE_DASH'],
			['_BAR_FILL', 'BAR_FILL'],
			['_RING_HOLE', 'RING_HOLE']
		]) {
			const a = table(py, pyName, pyRx(pyName));
			const b = table(ts, tsName, tsRx(tsName));
			expect(a.length, `${pyName} parsed as empty`).toBeGreaterThan(1);
			expect(b, `${pyName} drifted between chart-preview.ts and charts.py`).toEqual(a);
		}
	});
	it('these stay literal — a token here would resolve to nothing in WeasyPrint', () => {
		// Guards the intent, not just the value: if someone "tokenises" the palette the preview
		// still renders in the browser (var() resolves) while the PDF loses all colour, which is
		// precisely the failure this file is here to prevent.
		for (const c of [...DEFAULT_PALETTE, ...Object.values(RENDER_CHROME)]) {
			expect(c).not.toMatch(/var\(|currentcolor|oklch|color-mix/i);
		}
	});

	it('both sides fit the drawing to the container the same way', () => {
		// `preserveAspectRatio` belongs to this contract as much as the palette does: if one side
		// fits the drawing inside the box and centres it while the other stretches it to fill, the
		// editor's preview is a different picture from the PDF at every size but one.
		//
		// Anchored on the EMISSION — the `width="100%" height="100%" preserveAspectRatio="…"` run that
		// both files write into the SVG root — and not on the first mention of the attribute anywhere
		// in the file. Both docstrings name the value they replaced, in quotes, so a looser pattern
		// read the prose and reported drift between a comment and a comment. The commentary either
		// side of a decision must be free to explain it without failing the test that pins it.
		const aspect = (src: string, name: string): string => {
			const m = /width="100%" height="100%" preserveAspectRatio="([^"]+)"/.exec(src);
			expect(m, `${name} no longer sets preserveAspectRatio on the SVG root`).not.toBeNull();
			return m![1];
		};
		const py = aspect(readFileSync(CHARTS_PY, 'utf8'), 'charts.py');
		expect(py, 'preserveAspectRatio drifted between chart-preview.ts and charts.py').toBe(
			aspect(readFileSync(PREVIEW_TS, 'utf8'), 'chart-preview.ts')
		);
		// And that way is `meet`, which NEVER DISTORTS. The chart is laid out at the box it was drawn
		// at, so the viewBox equals the container and there is nothing to fit; `meet` stays as the
		// safe degradation for a residual mismatch, where empty space beats smeared text. `none` was
		// tried and removed the letterbox bands by distorting the drawing — the symptom, not the
		// cause, which was a marker that carried no width at all.
		expect(py).toBe('xMidYMid meet');
	});

	it('both sides read the same axis-title props', () => {
		// These two were collected by the drawer, stored by `serialize.ts`, and drawn by NEITHER
		// renderer — so a label an author typed simply vanished. Pinned on both sides because a label
		// drawn in the preview and not in the PDF is the same defect wearing a different hat.
		for (const [name, src] of [
			['charts.py', readFileSync(CHARTS_PY, 'utf8')],
			['chart-preview.ts', readFileSync(PREVIEW_TS, 'utf8')]
		] as const) {
			expect(src, `${name} no longer reads x_label`).toContain('x_label');
			expect(src, `${name} no longer reads y_label`).toContain('y_label');
			// Rotated, which is what makes a y title readable beside the ticks.
			expect(src, `${name} no longer rotates the y title`).toContain('rotate(-90');
		}
	});

	it('offers the same closed set of figure formats', () => {
		// An author picks from this list and an unrecognised value falls back to `number` rather than
		// reaching a format string — so the two sides agreeing on the MEMBERS is the whole guarantee.
		// A format the preview honours and the worker does not is a chart that reads one way on the
		// page and another in the document.
		const block = /^VALUE_FORMATS = \(([^)]*)\)/m.exec(readFileSync(CHARTS_PY, 'utf8'));
		expect(block, 'VALUE_FORMATS not found in charts.py').not.toBeNull();
		const py = [...block![1].matchAll(/"([a-z]+)"/g)].map((m) => m[1]);
		expect([...VALUE_FORMATS]).toEqual(py);
		// A real spread, so an empty parse cannot pass the comparison above.
		expect(py).toContain('currency');
	});

	it('agrees on the currency symbol', () => {
		// £ is the platform's currency (British English throughout). A chart needing another one
		// needs a `currency_symbol` prop; what it must not have is one symbol in the editor and
		// another in the PDF.
		for (const [name, src] of [
			['charts.py', readFileSync(CHARTS_PY, 'utf8')],
			['chart-preview.ts', readFileSync(PREVIEW_TS, 'utf8')]
		] as const) {
			expect(src, `${name} no longer formats currency`).toContain('£');
		}
	});
});
