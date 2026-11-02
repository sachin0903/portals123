// A CLOSED list: a filter the send environment does not register is a `TemplateAssertionError` at SEND time, so `field-filters.test.ts` reads `app/render/jinja_engine.py` and fails when this list names one the platform lacks.
// Deliberately absent — `sum_currency` (a required argument the panel cannot supply ⇒ an unrenderable template), the `join_list`/`truncate_text` aliases, Jinja's builtins (they render the word "None" where these answer `''`), and arguments.

export interface FieldFilter {
	/** The Jinja filter name, exactly as `jinja_engine.py` registers it. */
	value: string;
	label: string;
}

export const FIELD_FILTERS: readonly FieldFilter[] = [
	{ value: 'currency', label: 'Money — £1,234.56' },
	{ value: 'percentage', label: 'Percentage — 12.34%' },
	{ value: 'number', label: 'Number — 1,234.56' },
	{ value: 'date', label: 'Date — 24 August 2026' },
	{ value: 'join', label: 'List — a, b, c' },
	{ value: 'truncate', label: 'Shortened — first 100 characters' },
	{ value: 'lower', label: 'Lower case — jane smith' },
	{ value: 'paragraphs', label: 'Paragraphs — typed free text' }
];

// The Format control's unset option, as a PAIR: the value a field carries when it has no format, and
// the words the panel puts against it. Both are exported because the control that offers the formats
// has to name that option too, and a second `''` and a second copy of the label beside it is how a
// control starts describing a state the model does not have — `describeFilter` answers off these, so
// the option in the menu and the summary of a chosen format are the same two strings.
export const NO_FILTER = '';
export const NO_FILTER_LABEL = 'No formatting';

// A missing value renders as NOTHING, so the email goes out saying "Hi ,". A filter and not `or`: `{{ amount or '—' }}` fires on anything falsy, reporting a real £0.00 as "—", where `fallback` (registered in `app/render/jinja_engine.py`) fires only on None, Undefined and whitespace.
// It goes LAST in the chain, so `{{ amount | currency | fallback('—') }}` is asked about `"£0.00"` and a zero keeps its formatting — which is what lets a fallback be offered on every field. `parseFieldToken` refuses the other order.
export const FALLBACK_FILTER = 'fallback';

/** Backslash first, or the escape we add for the quote is itself escaped and the literal ends early. */
function quoteFallback(text: string): string {
	return `'${text.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;
}

function unquoteFallback(inner: string): string {
	return inner.replace(/\\(['\\])/g, '$1');
}

// Spaced exactly as `merge-fields.ts` writes its tokens: the two sit side by side in the same copy, where a difference in spacing reads as a difference in meaning.
export function fieldToken(
	field: string,
	filter?: string | null,
	fallback?: string | null
): string {
	const path = field.trim();
	if (path === '') return '';
	const name = (filter ?? '').trim();
	// NOT trimmed: a single space is a legitimate fallback (it keeps a sentence's spacing), and blank is what means "no fallback".
	const spare = fallback ?? '';
	const chain = [name, spare === '' ? '' : `${FALLBACK_FILTER}(${quoteFallback(spare)})`].filter(
		(part) => part !== ''
	);
	return chain.length === 0 ? `{{ ${path} }}` : `{{ ${path} | ${chain.join(' | ')} }}`;
}

// The grammar is exactly what `fieldToken` emits and no other; anything else returns null, so the panel leaves an author's own Jinja alone. The fallback comes off the END FIRST, before the pipes are counted, because its argument may contain one.
export function parseFieldToken(raw: string | null | undefined): {
	field: string;
	filter: string;
	fallback: string;
} | null {
	const text = String(raw ?? '').trim();
	if (!text.startsWith('{{') || !text.endsWith('}}')) return null;
	let inner = text.slice(2, -2).trim();
	if (inner === '') return null;
	// The trailing `| fallback('…')`, quote-aware so an escaped quote inside it survives.
	let fallback = '';
	const tail = new RegExp(
		`\\|\\s*${FALLBACK_FILTER}\\(\\s*'((?:\\\\.|[^'\\\\])*)'\\s*\\)\\s*$`
	).exec(inner);
	if (tail) {
		fallback = unquoteFallback(tail[1] ?? '');
		inner = inner.slice(0, tail.index).trim();
		if (inner === '') return null;
	}
	// One pipe left: a chain (`{{ x | lower | truncate }}`) is valid Jinja the panel cannot represent.
	const parts = inner.split('|');
	if (parts.length > 2) return null;
	const field = (parts[0] ?? '').trim();
	const filter = (parts[1] ?? '').trim();
	if (field === '') return null;
	// An argument on the format cannot be round-tripped, and a second `fallback(…)` lands here too, so `fallback('a') | fallback('b')` is refused rather than half-read.
	if (filter.includes('(') || field.includes('(')) return null;
	return { field, filter, fallback };
}

export function describeFallback(fallback: string | null | undefined): string {
	const text = fallback ?? '';
	if (text === '') return 'With no value and no fallback, this renders as nothing.';
	return `Shown when there is no value. A real 0 is not "no value".`;
}

export function describeFilter(filter: string | null | undefined): string {
	const name = (filter ?? '').trim();
	if (name === NO_FILTER) return NO_FILTER_LABEL;
	return FIELD_FILTERS.find((f) => f.value === name)?.label ?? name;
}

/** The shape of a field as the panel holds it: its name, its filter and its fallback. */
export interface FieldParts {
	field: string;
	filter: string;
	fallback: string;
}

/** What a field edit WRITES — the structured attributes and the Jinja token, from one state.
 *
 *  Extracted from the editor's `writeField` so the invariant is testable: the attributes are what the
 *  panel reads back and the token is what the send EVALUATES, so a path that updated one without the
 *  other would leave a chip describing itself as one thing and mailing another. Both come from this,
 *  and the editor writes exactly what it returns.
 *
 *  A blank filter or fallback answers `null`, not `''`: an empty attribute and an absent one would be
 *  two spellings of "no filter", which is how a panel starts disagreeing with itself
 *  (`field-component.test.ts` pins the same rule on `fieldSpanHtml`).
 */
export function fieldWrite(
	current: FieldParts,
	patch: Partial<FieldParts>,
	attrs: { field: string; filter: string; fallback: string }
): { next: FieldParts; attributes: Record<string, string | null>; token: string } {
	const next: FieldParts = { ...current, ...patch };
	return {
		next,
		attributes: {
			[attrs.field]: next.field,
			[attrs.filter]: next.filter || null,
			[attrs.fallback]: next.fallback || null
		},
		token: fieldToken(next.field, next.filter, next.fallback)
	};
}
