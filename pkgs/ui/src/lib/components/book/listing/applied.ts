// What is narrowing the list right now, as text — the arithmetic behind the applied-filter bar.
import { formatDate } from '../../../dates.js';
import { formatMoney } from '../../../money.js';

/** Structural, not an import of `FilterDef`: a `.ts` reaching into the component it serves cycles. */
export type FilterVocabulary = {
	key: string;
	label: string;
	options: readonly { value: string; label: string }[];
	/** `daterange` reads the pair as [since, until], `numberrange` as [from, to]. */
	kind?: 'values' | 'daterange' | 'numberrange';
	currency?: string;
	unit?: string;
};

export type AppliedFilter = { key: string; label: string; text: string };

const NAMES_PER_CHIP = 2;

const NUMBER = new Intl.NumberFormat('en-GB');

function numberRangeText(
	from: string | undefined,
	to: string | undefined,
	currency?: string,
	unit?: string
): string {
	// From a hand-editable URL: a bound that is not a number must narrow nothing, not print NaN.
	const show = (raw: string | undefined): string => {
		if (raw === undefined || raw.trim() === '') return '';
		const n = Number(raw);
		if (!Number.isFinite(n)) return '';
		if (currency) return formatMoney(n, currency);
		return NUMBER.format(n) + (unit ?? '');
	};
	const lo = show(from);
	const hi = show(to);
	if (lo && hi) return `${lo} – ${hi}`;
	if (lo) return `from ${lo}`;
	if (hi) return `up to ${hi}`;
	return '';
}

export function dateRangeLabel(since: string | undefined, until: string | undefined): string {
	const from = since ? formatDate(since) : '';
	const to = until ? formatDate(until) : '';
	if (from && to) return `${from} – ${to}`;
	if (from) return `from ${from}`;
	if (to) return `until ${to}`;
	return '';
}

/** A pair with an empty slot is a RANGE, and its order is its meaning — the same rule
 *  `appendFilter` and `queryFromParams` keep. */
const positional = (values: readonly string[]): boolean =>
	values.length === 2 && values.some((v) => v === '') && values.some(Boolean);

/** Order is ignored for a multi-select, whose values are a SET — comparing in order reports
 *  "changed" on a draft asking exactly what the list already shows. NOT for a range: sorting
 *  `['3000','']` and `['','3000']` makes "from 3,000" and "up to 3,000" identical, so moving the
 *  figure from one end to the other looked like no change and never reached the listing. */
export function sameFilters(a: Record<string, string[]>, b: Record<string, string[]>): boolean {
	const keys = Object.keys(a).filter((k) => a[k]?.length);
	const other = Object.keys(b).filter((k) => b[k]?.length);
	if (keys.length !== other.length) return false;
	return keys.every((key) => {
		const left = a[key] ?? [];
		const right = b[key] ?? [];
		if (left.length !== right.length) return false;
		const [x, y] =
			positional(left) || positional(right) ? [left, right] : [[...left].sort(), [...right].sort()];
		return x.every((v, i) => v === y[i]);
	});
}

export function countFilters(
	value: Record<string, string[]>,
	/** GIVEN, the count is what the bar will draw. Without them a RANGE holding what was TYPED
	 *  (`['abc', '']`) counts, and the badge read "1 applied" over a bar with no chip. */
	available?: readonly FilterVocabulary[]
): number {
	if (available) return appliedFilters(available, value).length;
	return Object.keys(value).filter((k) => value[k]?.length).length;
}

/** Chips, in the order the listing DEFINES its filters. A value with no matching option prints as
 *  itself: a server-driven listing holds one out of the URL before its facets arrive. */
export function appliedFilters(
	available: readonly FilterVocabulary[],
	value: Record<string, string[]>
): AppliedFilter[] {
	const out: AppliedFilter[] = [];
	for (const filter of available) {
		const picked = value[filter.key] ?? [];
		if (!picked.length) continue;
		// Either range is ONE fact about two bounds — one chip, one on the badge.
		if (filter.kind === 'numberrange') {
			const text = numberRangeText(picked[0], picked[1], filter.currency, filter.unit);
			if (text) out.push({ key: filter.key, label: filter.label, text });
			continue;
		}
		if (filter.kind === 'daterange') {
			const text = dateRangeLabel(picked[0], picked[1]);
			if (text) out.push({ key: filter.key, label: filter.label, text });
			continue;
		}
		const names = picked.map((v) => filter.options.find((o) => o.value === v)?.label ?? v);
		const head = names.slice(0, NAMES_PER_CHIP).join(', ');
		const rest = names.length - NAMES_PER_CHIP;
		out.push({
			key: filter.key,
			label: filter.label,
			text: rest > 0 ? `${head} +${rest}` : head
		});
	}
	return out;
}

/** Both ends INCLUSIVE, and a bound that is empty or not a number is no bound at all. */
export function withinRange(figure: number, pair: readonly string[] | undefined): boolean {
	if (!pair?.length) return true;
	const bound = (raw: string | undefined): number | undefined => {
		if (raw === undefined || raw.trim() === '') return undefined;
		const n = Number(raw);
		return Number.isFinite(n) ? n : undefined;
	};
	const lo = bound(pair[0]);
	const hi = bound(pair[1]);
	if (!Number.isFinite(figure)) return lo === undefined && hi === undefined;
	if (lo !== undefined && figure < lo) return false;
	if (hi !== undefined && figure > hi) return false;
	return true;
}

/** A slider's track, rounded to TWO significant figures. Straight from the data the top lands just
 *  BELOW the largest row, so dragging to the end drops the biggest clients — and the ends then
 *  miss the handles' starting positions, so opening the panel writes bounds nobody chose. */
export function niceRange(
	min: number,
	max: number
): { min: number; max: number; step: number } | null {
	if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return null;
	const lo = min >= 0 ? 0 : -niceUp(-min);
	const hi = niceUp(max);
	if (hi <= lo) return null;
	// NOT rounded: the remainder of a step that does not divide the span is the gap that put the
	// maximum out of the handle's reach.
	return { min: lo, max: hi, step: (hi - lo) / 100 };
}

function niceUp(value: number): number {
	if (value <= 0) return 0;
	const magnitude = 10 ** (Math.floor(Math.log10(value)) - 1);
	return Math.ceil(value / magnitude) * magnitude;
}

/** A handle at an end means NO BOUND — dragging it there is how a range is cleared. "At an end"
 *  allows HALF A STEP of slack: the nearest stop need not land on the boundary, and without it the
 *  last stop is a bound nobody chose. */
export function handlesToPair(
	handles: readonly number[],
	track: { min: number; max: number; step: number }
): string[] {
	const lo = handles[0] ?? track.min;
	const hi = handles[1] ?? track.max;
	const slack = track.step / 2;
	const out = (n: number) => String(track.step >= 1 ? Math.round(n) : n);
	const from = lo <= track.min + slack ? '' : out(lo);
	const to = hi >= track.max - slack ? '' : out(hi);
	return from || to ? [from, to] : [];
}

/** The nearest stop on a track. A tie goes to the LOWER stop: that is how the slider walks its
 *  stops, and the two have to agree or a typed amount and the handle that "corrects" it never
 *  compare equal. */
export function snapToTrack(
	value: number,
	track: { min: number; max: number; step: number }
): number {
	const { min, max, step } = track;
	if (!(step > 0) || !(max > min) || !Number.isFinite(value)) return value;
	const places = stepDecimals(step);
	const index = Math.floor((value - min) / step);
	const lower = roundTo(min + index * step, places);
	const upper = roundTo(lower + step, places);
	const pick = value - lower <= upper - value ? lower : upper;
	return Math.min(max, Math.max(min, pick));
}

function stepDecimals(step: number): number {
	if (!Number.isFinite(step) || Math.floor(step) === step) return 0;
	const text = step.toString();
	if (text.includes('e-')) return Number(text.split('e-')[1]);
	const dot = text.indexOf('.');
	return dot === -1 ? 0 : text.length - dot - 1;
}

function roundTo(value: number, places: number): number {
	const factor = 10 ** places;
	return Math.round(value * factor) / factor;
}

/** What a slider write means for the boxes.
 *
 *  `null` means the write is only the track snapping the amounts already typed — an exact figure
 *  is rarely on a step, and one within half a step of an end is then read as no bound at all, so
 *  accepting it would clear a number the user just entered.
 *
 *  A handle that did not move keeps its typed text. The slider only knows the stop, so a drag of
 *  the other end would otherwise report the snap and wipe an exact amount (100 beside a moved
 *  maximum became no minimum). A handle that did move is read by `handlesToPair`, including ''
 *  when it was dragged back to an end. */
export function rangeFromSlider(
	next: readonly number[],
	typed: readonly string[],
	track: { min: number; max: number; step: number }
): string[] | null {
	const placed = (raw: string | undefined, fallback: number) => {
		if (raw === undefined || raw.trim() === '') return fallback;
		const n = Number(raw);
		return Number.isFinite(n) ? n : fallback;
	};
	const current = [
		snapToTrack(placed(typed[0], track.min), track),
		snapToTrack(placed(typed[1], track.max), track)
	];
	// Indexed access is `number | undefined` even on a pair we just built.
	const currentFrom = current[0] ?? track.min;
	const currentTo = current[1] ?? track.max;
	const nextFrom = next[0] ?? currentFrom;
	const nextTo = next[1] ?? currentTo;
	const same = (a: number, b: number) => Math.abs(a - b) <= Math.max(track.step * 1e-6, 1e-9);
	if (same(nextFrom, currentFrom) && same(nextTo, currentTo)) {
		return null;
	}
	const dragged = handlesToPair(next, track);
	const from = same(nextFrom, currentFrom) ? (typed[0] ?? '') : (dragged[0] ?? '');
	const to = same(nextTo, currentTo) ? (typed[1] ?? '') : (dragged[1] ?? '');
	return capFromTo([from, to]);
}

/** From may not sit above To. An empty To is "no ceiling", so From is left alone; once To is a
 *  number, From is rewritten to that same text rather than a second formatting of it. */
export function capFromTo(pair: readonly string[]): string[] {
	const from = (pair[0] ?? '').trim();
	const to = (pair[1] ?? '').trim();
	if (!from || !to) return from || to ? [from, to] : [];
	const lo = Number(from);
	const hi = Number(to);
	if (!Number.isFinite(lo) || !Number.isFinite(hi) || lo <= hi) return [from, to];
	return [to, to];
}
