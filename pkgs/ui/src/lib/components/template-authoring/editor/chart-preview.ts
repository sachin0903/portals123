/**
 * Client-side chart → inline SVG for the editor's config preview. A faithful port of the backend
 * `app/render/charts.py` so what the adviser sees while configuring a chart matches what the render
 * worker bakes into the PDF. Pure functions of (component, rows, size) — no I/O.
 *
 * The preview is fed the SAME resolved rows the worker uses (via GET /datasets/{uid}/data), so a
 * blank/"no data" preview is a real signal that the dataset isn't linking up — the whole point of
 * showing it here.
 */

export interface PreviewBinding {
	/** The dataset the component binds, as `{{ code }}` — what the editor round-trips through
	    `data-dataset` and what the render path reads (`empty_state.dataset_code`). Declared here
	    because the empty-state caption names the dataset. */
	_resolved_data?: string;
	x_field?: string;
	y_field?: string | string[];
	series_fields?: string[];
	label_field?: string;
	value_field?: string;
}
export interface PreviewProps {
	/** Axis titles. Mirrors `charts.py`; the drawer collected these long before anything drew them. */
	x_label?: string;
	y_label?: string;
	/** How a figure reads: `number` | `currency` | `percent`, and to how many decimals. */
	value_format?: string;
	value_decimals?: unknown;
	/** Print each figure on the chart. Bars and line points only — see `charts.py`. */
	show_values?: boolean;
	title?: string;
	colors?: string[] | string;
	show_grid?: boolean;
	show_legend?: boolean;
	line_width?: number;
	/** Typography — the font stack, a base size every other size scales from, and the text colour. */
	font_family?: string;
	font_size?: unknown;
	text_color?: string;
	/** The chart's own background paint. Blank ⇒ transparent, as it has always drawn. */
	background?: string;
	/** The gridlines, and the axis with them. Blank ⇒ the default pair. */
	grid_color?: string;
	/** Per-type shape. Each falls back to what these charts have always drawn — see `charts.py`. */
	line_style?: string;
	show_points?: unknown;
	area_fill?: unknown;
	bar_width?: string;
	bar_corners?: string;
	ring_width?: string;
}
export interface PreviewComp {
	type: string;
	props?: PreviewProps;
	binding?: PreviewBinding;
}
export type PreviewRow = Record<string, unknown>;

// Default series palette — brand teal + ink-navy lead, then a categorical spread. Mirrors
// charts.py DEFAULT_PALETTE exactly (editor preview must match the PDF render).
//
// These are deliberately LITERAL hex, not design tokens. A chart here is document content: the same
// bytes are handed to the render worker, which draws the PDF with WeasyPrint — no app stylesheet, no
// custom properties, no light/dark. A token would resolve to nothing server-side, and a chart that
// followed the adviser's colour scheme would misrepresent the artefact they are about to send a
// client. Exported so chart-preview.contract.test.ts can assert they still match charts.py, since
// "kept in sync" by comment alone is exactly how the two silently drifted apart before.
export const DEFAULT_PALETTE = [
	'#0f766e',
	'#1e3a5f',
	'#3b82f6',
	'#f59e0b',
	'#8b5cf6',
	'#10b981',
	'#ef4444',
	'#ec4899'
];
const GRID = '#eef2f6';
const AXIS = '#cbd5e1';
const TEXT = '#1e293b';
const MUTED = '#94a3b8';
// Default card chrome drawn behind every chart — mirrors charts.py (_CARD_BG / _CARD_BORDER).
// TRANSPARENT: the background belongs to the frame div, which `chart-frame` serialises with the
// author's own padding/background/border around the marker, so a white slab here painted over it.
// `none` is a paint, not a colour — the rect is still drawn for its stroke.
const CARD_BG = 'none';
const CARD_BORDER = '#e2e8f0';
// Chart text. Mirrors charts.py (_FONT_FAMILY / _BASE_FONT): the family is a STACK, since the render
// container resolves against the fonts it has installed, and every size in this file is a multiple of
// the base so one control resizes all of a chart's text in proportion. 9 is the axis-tick size — the
// smallest thing a reader must still be able to read.
const FONT_FAMILY = 'Arial, sans-serif';
const BASE_FONT = 9;

/** The fonts a chart may be set in. STACKS, not faces: the PDF is drawn by WeasyPrint against the
 *  fonts installed in the render container, so each entry ends in a generic family it can always
 *  fall back to. Kept short and web-safe on purpose — a font the container does not hold renders as
 *  the fallback, which is a chart that quietly ignores the setting. `''` is the module default. */
export const CHART_FONTS: readonly { value: string; label: string }[] = [
	{ value: '', label: 'Default' },
	{ value: 'Arial, sans-serif', label: 'Arial' },
	{ value: 'Helvetica, Arial, sans-serif', label: 'Helvetica' },
	{ value: 'Verdana, sans-serif', label: 'Verdana' },
	{ value: 'Tahoma, sans-serif', label: 'Tahoma' },
	{ value: 'Georgia, serif', label: 'Georgia' },
	{ value: '"Times New Roman", Times, serif', label: 'Times New Roman' },
	{ value: '"Courier New", Courier, monospace', label: 'Courier New' }
];

/** Resolved chart typography — the font stack, the base size, and the two text colours. */
interface Typo {
	family: string;
	base: number;
	text: string;
	muted: string;
}

/** Mirrors `charts.py::_typography`, including the rule that MUTED FOLLOWS TEXT ONLY WHEN TEXT WAS
 *  CHOSEN: a fixed grey is only quiet against a light card, so a chart given white text on a dark
 *  panel would keep an unreadable axis — while deriving it unconditionally would re-tone the ticks of
 *  every chart already authored. */
function typography(props: PreviewProps): Typo {
	const family = (props.font_family ?? '').trim() || FONT_FAMILY;
	const text = (props.text_color ?? '').trim() || TEXT;
	const base = toNumber(props.font_size) ?? 0;
	return {
		family,
		base: base > 0 ? base : BASE_FONT,
		text,
		muted: text === TEXT ? MUTED : text
	};
}

// Per-type shape controls, mirroring `charts.py`. Each is a NAMED STEP rather than a free number:
// a chart is read at a glance in a printed document, and "bar width 0.83" is a question nobody can
// answer about their own report. The first entry of each is what these charts have always drawn.
const LINE_DASH: Record<string, string> = { solid: '', dashed: '7 5', dotted: '1 4' };
const BAR_FILL: Record<string, number> = { medium: 0.8, narrow: 0.5, wide: 0.95 };
const RING_HOLE: Record<string, number> = { medium: 0.58, thin: 0.72, thick: 0.4 };

/** One of a named set, falling back to the first entry — which is always today's drawing. */
function step<T>(value: unknown, table: Record<string, T>): T {
	const name = String(value ?? '')
		.trim()
		.toLowerCase();
	return name in table ? table[name]! : Object.values(table)[0]!;
}

/** An explicitly-on switch. Absent is OFF for every one of these: each adds ink to the figure, and a
 *  chart already in a client's report must not gain marks because a feature shipped. */
const on = (value: unknown): boolean =>
	['true', '1', 'yes', 'on'].includes(
		String(value ?? '')
			.trim()
			.toLowerCase()
	);

/** The gridlines and the axis lines, as one decision. Mirrors `charts.py::_grid_ink`, including the
 *  rule that THE AXIS FOLLOWS THE GRID ONLY WHEN THE GRID WAS CHOSEN: they are the chart's structural
 *  ink, and an author who could darken one but not the other would get a pale L drawn around a dark
 *  grid — while deriving it unconditionally would re-tone the axis of every chart already authored. */
function gridInk(props: PreviewProps): [string, string] {
	const grid = (props.grid_color ?? '').trim();
	return [grid || GRID, grid || AXIS];
}

/** The chart's own background paint. Mirrors `charts.py::_card_bg`: it belongs to the FIGURE now,
 *  not to the wrapper div, so it travels with the chart and is set beside the series colours it sits
 *  behind rather than two panels away in the style rail. */
const cardBg = (props: PreviewProps): string => (props.background ?? '').trim() || CARD_BG;

/** A font size scaled from the author's base. `canonical` is the size at the default base, so an
 *  untouched chart draws the exact numbers this file has always written. */
const fs = (t: Typo, canonical: number): string => fmt((t.base * canonical) / BASE_FONT);
/** The non-series document-render literals, keyed by their charts.py counterpart. Exported for the
 *  contract test only — drawing code uses the consts directly. */
export const RENDER_CHROME = {
	_GRID: GRID,
	_AXIS: AXIS,
	_TEXT: TEXT,
	_MUTED: MUTED,
	_CARD_BG: CARD_BG,
	_CARD_BORDER: CARD_BORDER
} as const;

/** Coerce a cell into a number — strips currency symbols, thousands commas and % so "£45,000"
 *  and "12.5%" resolve. Mirrors resolve.py's to_number. */
export function toNumber(v: unknown): number | null {
	if (v === null || v === undefined) return null;
	if (typeof v === 'number') return Number.isFinite(v) ? v : null;
	if (typeof v === 'boolean') return v ? 1 : 0;
	const s = String(v).trim();
	if (!s) return null;
	const cleaned = s.replace(/[^0-9.eE+-]/g, '');
	if (!cleaned || cleaned === '-' || cleaned === '+' || cleaned === '.') return null;
	const n = Number(cleaned);
	return Number.isFinite(n) ? n : null;
}

function esc(s: unknown): string {
	return String(s ?? '')
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;');
}

/** Compact coordinate string (1dp, trailing-zero-stripped). */
function fmt(v: number): string {
	return v.toFixed(1).replace(/0+$/, '').replace(/\.$/, '');
}

/** What a figure on a chart may be — closed, so an unrecognised value falls back to `number`
 *  rather than reaching a format string. Mirrors charts.py's `VALUE_FORMATS`. */
export const VALUE_FORMATS = ['number', 'currency', 'percent'] as const;

/** A tick or a data label. £ is the platform's currency (British English throughout); a chart that
 *  needs another one needs a `currency_symbol` prop, not a guess from the data. Mirrors
 *  charts.py::_value_text. `decimals` of null is the automatic behaviour `axisLabel` has always
 *  had; a number chosen explicitly is honoured exactly, because 0 is a real answer. */
function valueText(v: number, format = 'number', decimals: number | null = null): string {
	const body =
		decimals === null
			? axisLabel(v)
			: v.toLocaleString('en-GB', {
					minimumFractionDigits: decimals,
					maximumFractionDigits: decimals
				});
	if (format === 'currency') return `£${body}`;
	if (format === 'percent') return `${body}%`;
	return body;
}

/** Human y-axis tick label — thousands-separated, no noise decimals. */
function axisLabel(v: number): string {
	if (Number.isInteger(v)) return v.toLocaleString('en-GB');
	const stripped = v.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
	const [intPart, dec] = stripped.split('.');
	const withSep = Number(intPart).toLocaleString('en-GB');
	return dec ? `${withSep}.${dec}` : withSep;
}

function palette(props: PreviewProps): string[] {
	const colors = props.colors;
	if (Array.isArray(colors) && colors.length) return colors.map(String);
	if (typeof colors === 'string' && colors.trim()) {
		const parts = colors
			.split(',')
			.map((c) => c.trim())
			.filter(Boolean);
		if (parts.length) return parts;
	}
	return DEFAULT_PALETTE;
}

function yFields(binding: PreviewBinding): string[] {
	const y = binding.y_field;
	if (Array.isArray(y)) return y.filter(Boolean).map(String);
	return y ? [String(y)] : [];
}

/** Wrap chart body in a responsive SVG root (fills its container, preserves aspect). By default
    paints the card background/border behind the chart (`bg=false` for callers that draw their own
    panel, e.g. the placeholder). */
function frame(
	width: number,
	height: number,
	body: string,
	t: Typo,
	title = '',
	bg = true,
	card_bg: string = CARD_BG
): string {
	const card = bg
		? `<rect x="0.5" y="0.5" width="${fmt(width - 1)}" height="${fmt(height - 1)}" ` +
			`rx="8" fill="${card_bg}" stroke="${CARD_BORDER}"/>`
		: '';
	const head = title
		? `<text x="${fmt(width / 2)}" y="18" text-anchor="middle" font-size="${fs(t, 13)}" ` +
			`font-weight="600" fill="${t.text}" font-family="${esc(t.family)}">${esc(title)}</text>`
		: '';
	return (
		`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${fmt(width)} ${fmt(height)}" ` +
		// NOTHING IS STRETCHED: the chart is laid out AT the box it was drawn at, so a taller box is a
		// longer y axis rather than the same picture scaled unevenly. `meet` stays as the safe
		// degradation for a residual mismatch. Mirrors charts.py::_frame, which carries the reasoning;
		// both sides must say the same thing or the preview lies about the PDF.
		`width="100%" height="100%" preserveAspectRatio="xMidYMid meet" ` +
		`font-family="${esc(t.family)}">${card}${head}${body}</svg>`
	);
}

/** An empty chart: a DASHED, unfilled outline in the card's own footprint with a small quiet
    caption. Mirrors `app/render/charts.py::_placeholder` so the author sees in the editor what
    lands in the PDF — a filled grey slab read as a broken figure rather than as a deliberate
    empty state. */
function placeholder(
	width: number,
	height: number,
	title: string,
	message: string,
	t: Typo
): string {
	const body =
		`<rect x="0.5" y="0.5" width="${fmt(width - 1)}" height="${fmt(height - 1)}" rx="8" ` +
		`fill="none" stroke="${CARD_BORDER}" stroke-width="1" stroke-dasharray="4 4"/>` +
		`<text x="${fmt(width / 2)}" y="${fmt(height / 2)}" text-anchor="middle" ` +
		`font-size="${fs(t, 10)}" fill="${t.muted}" ` +
		`font-family="${esc(t.family)}">${esc(message)}</text>`;
	return frame(width, height, body, t, title, false);
}

/** The no-rows caption, worded exactly as the PDF words it (`empty_state.no_rows_message`): the
    same fact in both places must read the same, or it looks like two different faults.

    The FIELD prompts above it stay imperative ("Choose an X-axis field") and deliberately differ
    from the PDF's "This chart has no fields selected" — in the editor the author can act on it,
    and in a generated document nobody can. */
function noRowsCaption(binding: PreviewBinding): string {
	const resolved = String(binding._resolved_data ?? '');
	const code = /\{\{\s*(.+?)\s*\}\}/.exec(resolved)?.[1]?.trim() ?? '';
	const label =
		code
			.replace(/^book_/, '')
			.replace(/_/g, ' ')
			.trim() || 'data';
	return `No ${label} to show`;
}

/** A small set of round-ish gridline values spanning [lo, hi]. */
function niceTicks(lo: number, hi: number, count = 4): number[] {
	if (hi <= lo) hi = lo + 1;
	const raw = (hi - lo) / count;
	const mag = raw > 0 ? 10 ** Math.floor(Math.log10(raw)) : 1;
	let step = mag;
	for (const mult of [1, 2, 2.5, 5, 10]) {
		step = mult * mag;
		if (raw <= step) break;
	}
	const start = Math.floor(lo / step) * step;
	const ticks: number[] = [];
	let v = start;
	while (v <= hi + step * 0.5) {
		ticks.push(Math.round(v * 1e6) / 1e6);
		v += step;
	}
	return ticks;
}

function legend(
	labels: string[],
	pal: string[],
	x: number,
	y: number,
	maxW: number,
	t: Typo
): string {
	const parts: string[] = [];
	let cx = x;
	for (let i = 0; i < labels.length; i++) {
		if (cx > x + maxW - 60 && i) break; // wrap-guard: stop before overflow
		const label = String(labels[i]).slice(0, 16);
		parts.push(
			`<rect x="${fmt(cx)}" y="${fmt(y - 8)}" width="10" height="10" rx="2" ` +
				`fill="${pal[i % pal.length]}"/>` +
				`<text x="${fmt(cx + 14)}" y="${fmt(y + 1)}" font-size="${fs(t, 9)}" ` +
				`fill="${t.text}">${esc(label)}</text>`
		);
		cx += 24 + label.length * 5.4;
	}
	return parts.join('');
}

/** Line / bar / stacked-area on a shared axis frame. */
function cartesian(
	ctype: string,
	rows: PreviewRow[],
	binding: PreviewBinding,
	props: PreviewProps,
	width: number,
	height: number
): string {
	const xField = String(binding.x_field ?? '');
	const seriesFields =
		ctype === 'stacked_area_chart'
			? (binding.series_fields ?? []).filter(Boolean).map(String)
			: yFields(binding);
	const title = String(props.title ?? '');
	const typo = typography(props);
	// Distinct messages: an unfinished binding (no axis/value field chosen) is a different problem
	// from a dataset that genuinely returned nothing — the author needs to know which to fix.
	if (!xField) return placeholder(width, height, title, 'Choose an X-axis field', typo);
	if (!seriesFields.length) return placeholder(width, height, title, 'Choose a value field', typo);
	if (!rows.length) return placeholder(width, height, title, noRowsCaption(binding), typo);

	const pal = palette(props);
	const showGrid = props.show_grid !== false;
	const showLegend = props.show_legend !== false && seriesFields.length > 1;
	const categories = rows.map((r) => String(r[xField] ?? ''));
	const series: (number | null)[][] = seriesFields.map((f) => rows.map((r) => toNumber(r[f])));

	// AXIS TITLES — mirrors charts.py, which carries the reasoning. Each takes its own room from the
	// plot rather than overlapping the y ticks (`left - 6`) or the x categories (`bottom + 12`).
	const xLabel = String(props.x_label ?? '');
	const yLabel = String(props.y_label ?? '');
	const rawFmt = String(props.value_format ?? 'number');
	const valFmt = (VALUE_FORMATS as readonly string[]).includes(rawFmt) ? rawFmt : 'number';
	const rawDec = toNumber(props.value_decimals);
	const valDec = rawDec === null ? null : Math.max(0, Math.min(4, Math.round(rawDec)));
	// DATA LABELS, for bars and line points only — a figure inside a stacked band is unreadable
	// against the fill beneath it and a pie already states each share in its legend. Mirrors
	// charts.py, which carries the reasoning.
	const showValues =
		props.show_values === true && (ctype === 'bar_chart' || ctype === 'line_chart');
	const top = title ? 30 : 12;
	const bottom = height - (showLegend ? 48 : 30) - (xLabel ? 14 : 0);
	const left = 50 + (yLabel ? 14 : 0);
	const right = width - 14;
	const plotH = Math.max(bottom - top, 10);
	const plotW = Math.max(right - left, 10);

	let hi: number;
	if (ctype === 'stacked_area_chart') {
		const totals = categories.map((_, i) => series.reduce((s, ser) => s + (ser[i] ?? 0), 0));
		hi = totals.length ? Math.max(...totals) : 1;
	} else {
		const vals = series.flat().filter((v): v is number => v !== null);
		hi = vals.length ? Math.max(...vals) : 1;
	}
	const lo = 0;
	const ticks = niceTicks(lo, hi);
	hi = ticks.length ? Math.max(...ticks) : hi || 1;

	const yOf = (v: number): number => (hi > lo ? bottom - ((v - lo) / (hi - lo)) * plotH : bottom);

	const [gridInkColour, axisInk] = gridInk(props);
	const parts: string[] = [];
	// gridlines + y labels
	for (const t of ticks) {
		const y = yOf(t);
		if (showGrid)
			parts.push(
				`<line x1="${fmt(left)}" y1="${fmt(y)}" x2="${fmt(right)}" y2="${fmt(y)}" ` +
					`stroke="${gridInkColour}" stroke-width="1"/>`
			);
		parts.push(
			`<text x="${fmt(left - 6)}" y="${fmt(y + 3)}" text-anchor="end" ` +
				`font-size="${fs(typo, 9)}" ` +
				`fill="${typo.muted}">${esc(valueText(t, valFmt, valDec))}</text>`
		);
	}
	// axes
	parts.push(
		`<line x1="${fmt(left)}" y1="${fmt(top)}" x2="${fmt(left)}" y2="${fmt(bottom)}" ` +
			`stroke="${axisInk}" stroke-width="1"/>` +
			`<line x1="${fmt(left)}" y1="${fmt(bottom)}" x2="${fmt(right)}" y2="${fmt(bottom)}" ` +
			`stroke="${axisInk}" stroke-width="1"/>`
	);
	const n = categories.length;
	const slot = plotW / Math.max(n, 1);
	// x category labels (thinned if crowded)
	const stepLbl = Math.max(1, Math.ceil(n / 12));
	categories.forEach((cat, i) => {
		if (i % stepLbl) return;
		const cx = left + slot * (i + 0.5);
		parts.push(
			`<text x="${fmt(cx)}" y="${fmt(bottom + 12)}" text-anchor="middle" ` +
				`font-size="${fs(typo, 9)}" ` +
				`fill="${typo.muted}">${esc(cat.slice(0, 14))}</text>`
		);
	});

	if (xLabel) {
		parts.push(
			`<text x="${fmt((left + right) / 2)}" y="${fmt(bottom + 28)}" text-anchor="middle" ` +
				`font-size="${fs(typo, 10)}" fill="${typo.text}">${esc(xLabel)}</text>`
		);
	}
	if (yLabel) {
		// Rotated about its own anchor, the plot's vertical middle, so it stays centred at any height.
		const cy = (top + bottom) / 2;
		parts.push(
			`<text x="14" y="${fmt(cy)}" text-anchor="middle" font-size="${fs(typo, 10)}" ` +
				`fill="${typo.text}" ` +
				`transform="rotate(-90 14 ${fmt(cy)})">${esc(yLabel)}</text>`
		);
	}

	if (ctype === 'bar_chart') {
		const g = series.length;
		const barW = Math.min((slot * step(props.bar_width, BAR_FILL)) / g, 48);
		const groupW = barW * g;
		// Square corners are a real answer: a 2px radius on a 6px-wide bar reads as a rounded blob.
		const barRx =
			String(props.bar_corners ?? '')
				.trim()
				.toLowerCase() === 'square'
				? '0'
				: '2';
		for (let i = 0; i < n; i++) {
			const gx = left + slot * (i + 0.5) - groupW / 2;
			series.forEach((ser, j) => {
				const v = ser[i] ?? 0;
				const bh = hi > lo ? ((v - lo) / (hi - lo)) * plotH : 0;
				const x = gx + barW * j;
				parts.push(
					`<rect x="${fmt(x)}" y="${fmt(bottom - bh)}" width="${fmt(barW)}" ` +
						`height="${fmt(Math.max(bh, 0))}" fill="${pal[j % pal.length]}" rx="${barRx}"/>`
				);
				if (showValues && ser[i] != null) {
					// Above the bar, clamped inside the plot: a tall bar would print over the title.
					const ly = Math.max(bottom - bh - 4, top + 8);
					parts.push(
						`<text x="${fmt(x + barW / 2)}" y="${fmt(ly)}" text-anchor="middle" ` +
							`font-size="${fs(typo, 8)}" ` +
							`fill="${typo.text}">${esc(valueText(v, valFmt, valDec))}</text>`
					);
				}
			});
		}
	} else if (ctype === 'stacked_area_chart') {
		const cum = new Array(n).fill(0);
		series.forEach((ser, j) => {
			const topPts: [number, number][] = [];
			const botPts: [number, number][] = [];
			for (let i = 0; i < n; i++) {
				const cx = left + slot * (i + 0.5);
				const base = cum[i];
				cum[i] = base + (ser[i] ?? 0);
				topPts.push([cx, yOf(cum[i])]);
				botPts.push([cx, yOf(base)]);
			}
			const pts = topPts.concat(botPts.reverse());
			const poly = pts.map(([x, y]) => `${fmt(x)},${fmt(y)}`).join(' ');
			parts.push(`<polygon points="${poly}" fill="${pal[j % pal.length]}" fill-opacity="0.85"/>`);
		});
	} else {
		// line_chart
		const lw = toNumber(props.line_width) ?? 1.5;
		const dash =
			LINE_DASH[
				String(props.line_style ?? '')
					.trim()
					.toLowerCase()
			] ?? '';
		const dashAttr = dash ? ` stroke-dasharray="${dash}"` : '';
		const showPoints = on(props.show_points);
		// A marker has to clear the line it sits on, or it reads as a thickening of the stroke.
		const pointR = Math.max(2, lw * 1.5);
		series.forEach((ser, j) => {
			const pts: [number, number][] = [];
			ser.forEach((v, i) => {
				if (v !== null) pts.push([left + slot * (i + 0.5), yOf(v)]);
			});
			if (pts.length < 2) {
				for (const [x, y] of pts)
					parts.push(
						`<circle cx="${fmt(x)}" cy="${fmt(y)}" r="2.5" fill="${pal[j % pal.length]}"/>`
					);
				return;
			}
			const poly = pts.map(([x, y]) => `${fmt(x)},${fmt(y)}`).join(' ');
			const colour = pal[j % pal.length];
			// THE AREA GOES DOWN FIRST, under every line, so a second series is not hidden behind the
			// first one's wash. It closes on the AXIS rather than on the lowest point: a fill floating
			// above the baseline reads as a band, not as "how much".
			if (on(props.area_fill)) {
				const floor = `${fmt(pts[pts.length - 1]![0])},${fmt(bottom)} ${fmt(pts[0]![0])},${fmt(bottom)}`;
				parts.push(`<polygon points="${poly} ${floor}" fill="${colour}" fill-opacity="0.14"/>`);
			}
			parts.push(
				`<polyline points="${poly}" fill="none" stroke="${colour}" ` +
					`stroke-width="${fmt(lw)}" stroke-linejoin="round" ` +
					`stroke-linecap="round"${dashAttr}/>`
			);
			if (showPoints) {
				for (const [px, py] of pts) {
					parts.push(
						`<circle cx="${fmt(px)}" cy="${fmt(py)}" r="${fmt(pointR)}" fill="${colour}"/>`
					);
				}
			}
			if (showValues) {
				// Thinned by the SAME rule as the x categories, or a label lands where no category is.
				ser.forEach((v, i) => {
					if (v == null || i % stepLbl) return;
					const px = left + slot * (i + 0.5);
					const py = Math.max(yOf(v) - 6, top + 8);
					parts.push(
						`<text x="${fmt(px)}" y="${fmt(py)}" text-anchor="middle" ` +
							`font-size="${fs(typo, 8)}" ` +
							`fill="${typo.text}">${esc(valueText(v, valFmt, valDec))}</text>`
					);
				});
			}
		});
	}

	if (showLegend) parts.push(legend(seriesFields, pal, left, bottom + 22, width - left - 14, typo));
	return frame(width, height, parts.join(''), typo, title, true, cardBg(props));
}

function pie(
	rows: PreviewRow[],
	binding: PreviewBinding,
	props: PreviewProps,
	width: number,
	height: number,
	donut: boolean
): string {
	const title = String(props.title ?? '');
	const typo = typography(props);
	const labelField = String(binding.label_field ?? '');
	const valueField = String(binding.value_field ?? '');
	if (!valueField) return placeholder(width, height, title, 'Choose a value field', typo);
	if (!rows.length) return placeholder(width, height, title, noRowsCaption(binding), typo);
	let slices = rows.map(
		(r) => [String(r[labelField] ?? ''), toNumber(r[valueField]) ?? 0] as [string, number]
	);
	// A pie can't render a zero/negative slice; drop them but remember the count so we can note it
	// on the chart rather than silently misrepresenting the data.
	const droppedSlices = slices.filter(([, v]) => v <= 0).length;
	slices = slices.filter(([, v]) => v > 0);
	const total = slices.reduce((s, [, v]) => s + v, 0);
	if (total <= 0) return placeholder(width, height, title, 'All values are zero or negative', typo);

	const pal = palette(props);
	const showLegend = props.show_legend !== false;
	const top = title ? 24 : 8;
	const legendW = showLegend ? 130 : 0;
	const availW = width - legendW;
	const r = Math.max(Math.min(availW, height - top - 12) / 2 - 6, 10);
	const cx = availW / 2;
	const cy = top + (height - top) / 2;

	const parts: string[] = [];
	// A DONUT IS A STROKED RING, not wedges under a cover disc. That disc had to be painted, and
	// `#fff` painted it over whatever background the frame carries — CARD_BG's defect in miniature.
	// Stroking leaves the hole genuinely transparent; on a white page the two look identical.
	const hole = r * step(props.ring_width, RING_HOLE);
	const ringR = (r + hole) / 2;
	const ringW = r - hole;
	let angle = -Math.PI / 2; // start at 12 o'clock
	slices.forEach(([, v], i) => {
		const frac = v / total;
		const end = angle + frac * 2 * Math.PI;
		const color = pal[i % pal.length];
		if (frac >= 0.999) {
			parts.push(
				donut
					? `<circle cx="${fmt(cx)}" cy="${fmt(cy)}" r="${fmt(ringR)}" fill="none" ` +
							`stroke="${color}" stroke-width="${fmt(ringW)}"/>`
					: `<circle cx="${fmt(cx)}" cy="${fmt(cy)}" r="${fmt(r)}" fill="${color}"/>`
			);
		} else if (donut) {
			const x0 = cx + ringR * Math.cos(angle);
			const y0 = cy + ringR * Math.sin(angle);
			const x1 = cx + ringR * Math.cos(end);
			const y1 = cy + ringR * Math.sin(end);
			const large = end - angle > Math.PI ? 1 : 0;
			parts.push(
				`<path d="M ${fmt(x0)} ${fmt(y0)} ` +
					`A ${fmt(ringR)} ${fmt(ringR)} 0 ${large} 1 ${fmt(x1)} ${fmt(y1)}" ` +
					`fill="none" stroke="${color}" stroke-width="${fmt(ringW)}"/>`
			);
		} else {
			const x0 = cx + r * Math.cos(angle);
			const y0 = cy + r * Math.sin(angle);
			const x1 = cx + r * Math.cos(end);
			const y1 = cy + r * Math.sin(end);
			const large = end - angle > Math.PI ? 1 : 0;
			parts.push(
				`<path d="M ${fmt(cx)} ${fmt(cy)} L ${fmt(x0)} ${fmt(y0)} ` +
					`A ${fmt(r)} ${fmt(r)} 0 ${large} 1 ${fmt(x1)} ${fmt(y1)} Z" fill="${color}"/>`
			);
		}
		angle = end;
	});

	if (showLegend) {
		let ly = top + 6;
		slices.forEach(([lbl, v], i) => {
			const pct = (v / total) * 100;
			parts.push(
				`<rect x="${fmt(availW + 6)}" y="${fmt(ly - 8)}" width="10" height="10" rx="2" ` +
					`fill="${pal[i % pal.length]}"/>` +
					`<text x="${fmt(availW + 20)}" y="${fmt(ly + 1)}" font-size="${fs(typo, 9)}" ` +
					`fill="${typo.text}">` +
					`${esc(String(lbl).slice(0, 14))} (${pct.toFixed(0)}%)</text>`
			);
			ly += 16;
		});
	}
	if (droppedSlices > 0) {
		parts.push(
			`<text x="6" y="${fmt(height - 6)}" font-size="${fs(typo, 9)}" fill="${typo.text}">` +
				`${droppedSlices} zero/negative ${droppedSlices === 1 ? 'value' : 'values'} not shown</text>`
		);
	}
	return frame(width, height, parts.join(''), typo, title, true, cardBg(props));
}

/** Render a chart component + its resolved rows to a standalone inline SVG string. */
export function renderChartSvg(
	comp: PreviewComp,
	rows: PreviewRow[],
	width = 600,
	height = 340
): string {
	const ctype = String(comp.type || 'line_chart');
	const props = comp.props ?? {};
	const binding = comp.binding ?? {};
	if (ctype === 'pie_chart' || ctype === 'donut_chart')
		return pie(rows, binding, props, width, height, ctype === 'donut_chart');
	return cartesian(ctype, rows, binding, props, width, height);
}

/** A `PreviewComp` from a chart component's `data-comp-*` attributes.
 *
 * ONE MAPPING, shared by the canvas component's own render and the config drawer's preview. They
 * read the same attributes and must draw the same picture: two copies of this is how the drawer
 * ends up promising a chart the canvas does not show.
 *
 * `fallbackColours` is the tenant palette. It is a FALLBACK and not a default merged into the list:
 * a firm that has chosen its own colours has chosen them, and appending ours behind theirs would
 * silently colour a fifth series in a shade they never picked.
 */
export function previewCompFromAttrs(
	attrs: Record<string, string>,
	fallbackColours: readonly string[] = DEFAULT_PALETTE
): PreviewComp {
	const type = attrs['data-comp-type'] || 'line_chart';
	const list = (key: string): string[] =>
		(attrs[key] ?? '')
			.split(',')
			.map((v) => v.trim())
			.filter(Boolean);
	const custom = list('data-comp-colors');
	const yFields = list('data-comp-y-field');
	return {
		type,
		props: {
			title: attrs['data-comp-title'] || '',
			x_label: attrs['data-comp-x-label'] || '',
			y_label: attrs['data-comp-y-label'] || '',
			value_format: attrs['data-comp-value-format'] || 'number',
			value_decimals: attrs['data-comp-value-decimals'] ?? '',
			show_values: attrs['data-comp-show-values'] === 'true',
			colors: custom.length ? custom : [...fallbackColours],
			show_grid: (attrs['data-comp-show-grid'] ?? 'true') !== 'false',
			show_legend: (attrs['data-comp-show-legend'] ?? 'true') !== 'false',
			line_width: parseFloat(attrs['data-comp-line-width'] ?? '1.5') || 1.5,
			// Passed through BLANK when unset rather than defaulted here: `typography()` owns every
			// fallback, so the preview and the worker cannot disagree about what "unset" draws as.
			font_family: attrs['data-comp-font-family'] ?? '',
			font_size: attrs['data-comp-font-size'] ?? '',
			text_color: attrs['data-comp-text-color'] ?? '',
			background: attrs['data-comp-background'] ?? '',
			grid_color: attrs['data-comp-grid-color'] ?? '',
			line_style: attrs['data-comp-line-style'] ?? '',
			show_points: attrs['data-comp-show-points'] ?? '',
			area_fill: attrs['data-comp-area-fill'] ?? '',
			bar_width: attrs['data-comp-bar-width'] ?? '',
			bar_corners: attrs['data-comp-bar-corners'] ?? '',
			ring_width: attrs['data-comp-ring-width'] ?? ''
		},
		binding: {
			x_field: attrs['data-comp-x-field'] || '',
			y_field: yFields,
			series_fields: type === 'stacked_area_chart' ? yFields : [],
			label_field: attrs['data-comp-label-field'] || '',
			value_field: attrs['data-comp-value-field'] || ''
		}
	} as PreviewComp;
}

/** Every field a comp is bound to, in the order a preview would read them. Deduped, blanks gone. */
export function boundFields(comp: PreviewComp): string[] {
	const b = comp.binding ?? {};
	const y = Array.isArray(b.y_field) ? b.y_field : b.y_field ? [b.y_field] : [];
	const series = Array.isArray(b.series_fields) ? b.series_fields : [];
	return [...new Set([b.label_field, b.x_field, ...y, ...series, b.value_field])].filter(
		(f): f is string => !!f
	);
}
