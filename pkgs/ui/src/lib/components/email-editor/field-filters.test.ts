import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
	describeFallback,
	describeFilter,
	FALLBACK_FILTER,
	FIELD_FILTERS,
	fieldToken,
	NO_FILTER,
	NO_FILTER_LABEL,
	parseFieldToken
} from './field-filters';

const REPO_ROOT = fileURLToPath(new URL('../../../../../../', import.meta.url));
const ENGINE = join(REPO_ROOT, 'apps', 'api', 'app', 'render', 'jinja_engine.py');

/** A Python parameter as the source declares it: its name, and its default written exactly as given
 *  (`null` when it has none, which is what makes it REQUIRED). */
interface PyParam {
	name: string;
	fallback: string | null;
}

/** The text between the brackets of `def <fn>(…)`, read by walking to the matching bracket rather
 *  than to the end of the line — a wrapped signature is ordinary Python and a line-based read would
 *  see only the first parameter of one, i.e. would report every later parameter as absent. */
function signature(source: string, fn: string): string {
	const head = `def ${fn}(`;
	const at = source.indexOf(head);
	if (at < 0) throw new Error(`jinja_engine.py registers ${fn} but declares no def for it`);
	const start = at + head.length;
	let depth = 1;
	let quote = '';
	for (let i = start; i < source.length; i++) {
		const ch = source[i] as string;
		if (quote !== '') {
			if (ch === '\\') i += 1;
			else if (ch === quote) quote = '';
			continue;
		}
		if (ch === '"' || ch === "'") quote = ch;
		else if (ch === '(' || ch === '[' || ch === '{') depth += 1;
		else if (ch === ')' || ch === ']' || ch === '}') {
			depth -= 1;
			if (depth === 0) return source.slice(start, i);
		}
	}
	throw new Error(`def ${fn}( in jinja_engine.py has no closing bracket`);
}

/** Split on TOP-LEVEL commas only. A default may hold one of its own — `separator: str = ", "` is
 *  real, in `_fmt_join` — and a naive split reads the tail of such a literal as a further parameter
 *  with no default, i.e. as required, which would condemn a filter the panel legitimately offers. */
function splitTopLevel(text: string): string[] {
	const parts: string[] = [];
	let depth = 0;
	let quote = '';
	let start = 0;
	for (let i = 0; i < text.length; i++) {
		const ch = text[i] as string;
		if (quote !== '') {
			if (ch === '\\') i += 1;
			else if (ch === quote) quote = '';
			continue;
		}
		if (ch === '"' || ch === "'") quote = ch;
		else if (ch === '(' || ch === '[' || ch === '{') depth += 1;
		else if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
		else if (ch === ',' && depth === 0) {
			parts.push(text.slice(start, i));
			start = i + 1;
		}
	}
	parts.push(text.slice(start));
	return parts.map((part) => part.trim()).filter((part) => part !== '');
}

function parameters(source: string, fn: string): PyParam[] {
	return (
		splitTopLevel(signature(source, fn))
			// `*` and `/` are position markers, and `*args`/`**kwargs` are not parameters a caller can be
			// required to name — neither is a value the panel would have to supply.
			.filter((part) => !part.startsWith('*') && part !== '/')
			.map((part) => {
				const eq = part.indexOf('=');
				const declared = eq < 0 ? part : part.slice(0, eq);
				return {
					name: (declared.split(':')[0] as string).trim(),
					fallback: eq < 0 ? null : part.slice(eq + 1).trim()
				};
			})
	);
}

/** A Python default with the quotes taken off a string literal, so a label can be compared with the
 *  value the filter really uses. */
function literal(text: string | null): string | null {
	if (text === null) return null;
	const quoted = /^(['"])([\s\S]*)\1$/.exec(text.trim());
	return quoted ? (quoted[2] as string) : text.trim();
}

/** Everything this suite asks of `jinja_engine.py`, derived from its text in ONE place — so the
 *  fixture cases below exercise the very reader that judges the real engine. */
function readRegistry(source: string) {
	// Name → the function it is bound to. Capturing the BINDING and not merely the name is what makes
	// the alias rule and the argument rule derivable at all; the corollary is that a registration
	// written any other way (`env.filters.update({…})`, a lambda) is invisible here, which is what the
	// exact-name assertion below exists to catch.
	const bound = new Map<string, string>();
	for (const m of source.matchAll(/env\.filters\["([a-z_]+)"\]\s*=\s*(\w+)/g)) {
		bound.set(m[1] as string, m[2] as string);
	}
	const byFunction = new Map<string, string[]>();
	for (const [name, fn] of bound) byFunction.set(fn, [...(byFunction.get(fn) ?? []), name]);
	return {
		names: new Set(bound.keys()),
		/** Registry names bound to the same function: one behaviour under two spellings. */
		aliasGroups: [...byFunction.values()]
			.filter((names) => names.length > 1)
			.map((names) => [...names].sort().join('/'))
			.sort(),
		/** Registry names whose function takes a parameter beyond the value with NO default. The panel
		 *  can only pipe a bare `| name`, so offering one of these is a TypeError at SEND. */
		needsArgument: [...bound]
			.filter(([, fn]) => parameters(source, fn).some((p, i) => i > 0 && p.fallback === null))
			.map(([name]) => name)
			.sort(),
		/** A parameter's default exactly as the source writes it — several labels quote one. */
		defaultOf: (fn: string, param: string) =>
			parameters(source, fn).find((p) => p.name === param)?.fallback ?? null
	};
}

describe('the offered filters exist on the send side', () => {
	// An unregistered filter or fallback isn't blank at render — it's a TemplateAssertionError hours after a template looked fine — so this reads jinja_engine.py directly (pkgs/ui isn't shipped to the API), the same cross-language pin merge-fields.ts keeps on `_CODE_KEYS`.
	const engine = readFileSync(ENGINE, 'utf8');
	const registry = readRegistry(engine);
	const offered = FIELD_FILTERS.map((f) => f.value);

	it('reads the WHOLE registry, so every rule below is asked about all twelve', () => {
		// Exact, because a floor cannot see a halved parse. The reader matches one spelling of a
		// registration, and a refactor to `env.filters.update({…})`, a lambda or a rename shrinks this
		// set with nothing failing — at which point "does the platform register this" is asked of a
		// registry that has quietly stopped listing most of it. Twelve is what `get_env` registers;
		// change that function and this line together.
		expect([...registry.names].sort()).toEqual([
			'currency',
			'date',
			'fallback',
			'join',
			'join_list',
			'lower',
			'number',
			'paragraphs',
			'percentage',
			'sum_currency',
			'truncate',
			'truncate_text'
		]);
		expect(registry.names.size).toBe(12);
	});

	it('names only filters the platform registers', () => {
		const missing = FIELD_FILTERS.filter((f) => !registry.names.has(f.value)).map((f) => f.value);
		expect(missing).toEqual([]);
	});

	it('offers no filter that needs an argument', () => {
		// DERIVED from the Python `def`, never a list of names: the panel pipes a bare `| name`, so a
		// registered filter carrying a second required parameter is a TypeError AT SEND — on a real
		// client's email, hours after the template looked fine. Naming `sum_currency` here would police
		// the one such filter that exists today and say nothing about the next one added.
		expect(offered.filter((name) => registry.needsArgument.includes(name))).toEqual([]);
		// And the derivation is doing work: it finds the one the registry really does have.
		expect(registry.needsArgument).toContain('sum_currency');
	});

	it('offers one name per behaviour, and knows every alias pair there is', () => {
		// The pairs are derived, so a THIRD spelling of some behaviour fails here rather than being
		// offered beside the one it duplicates — two menu entries doing the same thing, which reads as
		// two formats to choose between.
		expect(registry.aliasGroups).toEqual(['join/join_list', 'truncate/truncate_text']);
		for (const group of registry.aliasGroups) {
			const offeredSpellings = group.split('/').filter((name) => offered.includes(name));
			expect(offeredSpellings.length, group).toBeLessThanOrEqual(1);
		}
		expect(offered).toContain('join');
		expect(offered).toContain('truncate');
	});

	it('describes every filter in the author’s words, never as its Jinja name', () => {
		for (const filter of FIELD_FILTERS) {
			expect(filter.label.length).toBeGreaterThan(filter.value.length);
			expect(filter.label).not.toBe(filter.value);
			expect(filter.label.charAt(0)).toBe(filter.label.charAt(0).toUpperCase());
		}
	});

	// A label that quotes a number or a symbol is a PROMISE about the platform's own default, and the
	// two sit in different languages a directory apart. Left unpinned, raising `_fmt_truncate`'s length
	// leaves the panel telling an author "first 100 characters" while the send cuts somewhere else.
	it('quotes the truncate length the filter actually uses', () => {
		const length = registry.defaultOf('_fmt_truncate', 'length');
		const label = FIELD_FILTERS.find((f) => f.value === 'truncate')?.label ?? '';
		expect(length).not.toBeNull();
		expect(/(\d+)/.exec(label)?.[1]).toBe(length);
	});

	it('quotes the currency symbol the filter actually uses', () => {
		const symbol = literal(registry.defaultOf('_fmt_currency', 'symbol'));
		const label = FIELD_FILTERS.find((f) => f.value === 'currency')?.label ?? '';
		const example = /—\s*(\S+)/.exec(label)?.[1] ?? '';
		expect(symbol).not.toBeNull();
		expect(example.slice(0, (symbol as string).length)).toBe(symbol);
	});
});

describe('reading the Python registry', () => {
	// The reader above is the whole of TWO safety rules, and it is parsing another language's source,
	// so it is exercised on shapes the real engine does not have YET: the required argument that gets
	// added next, an alias pair minted in passing, and a wrapped signature. A reader that quietly found
	// nothing would let every derived assertion pass by knowing about nothing.
	const FIXTURE = [
		'def _fmt_thing(value: object, separator: str = ", ") -> str:',
		'    return ""',
		'',
		'def _fmt_ratio(value: object, of: object) -> str:',
		'    return ""',
		'',
		'def _fmt_trim(value: object, length: int = 40) -> str:',
		'    return ""',
		'',
		'def _fmt_wrapped(',
		'    value: object,',
		'    factor: int,',
		') -> str:',
		'    return ""',
		'',
		'        env.filters["thing"] = _fmt_thing',
		'        env.filters["thingy"] = _fmt_thing',
		'        env.filters["ratio"] = _fmt_ratio',
		'        env.filters["trim"] = _fmt_trim',
		'        env.filters["wrapped"] = _fmt_wrapped'
	].join('\n');
	const fixture = readRegistry(FIXTURE);

	it('finds every registration', () => {
		expect([...fixture.names].sort()).toEqual(['ratio', 'thing', 'thingy', 'trim', 'wrapped']);
	});

	it('reports a NEW required-argument filter, whatever its name', () => {
		expect(fixture.needsArgument).toEqual(['ratio', 'wrapped']);
	});

	it('does not mistake a comma inside a default for a further parameter', () => {
		// `separator: str = ", "` is `_fmt_join`'s real signature: split naively, its closing quote reads
		// as a parameter with no default and `join` — which the panel offers — is condemned.
		expect(fixture.needsArgument).not.toContain('thing');
		expect(fixture.defaultOf('_fmt_thing', 'separator')).toBe('", "');
	});

	it('reports a NEW alias pair, whatever it aliases', () => {
		expect(fixture.aliasGroups).toEqual(['thing/thingy']);
	});

	it('reads a default off the source', () => {
		expect(fixture.defaultOf('_fmt_trim', 'length')).toBe('40');
		expect(literal(fixture.defaultOf('_fmt_thing', 'separator'))).toBe(', ');
		expect(fixture.defaultOf('_fmt_ratio', 'of')).toBeNull();
	});

	it('refuses to guess when a registered function has no def', () => {
		expect(() => readRegistry('env.filters["gone"] = _fmt_gone').needsArgument).toThrow(
			/_fmt_gone/
		);
	});
});

describe('fieldToken', () => {
	it('spaces a plain token exactly as merge-fields.ts writes one', () => {
		expect(fieldToken('first_name')).toBe('{{ first_name }}');
		expect(fieldToken('brand.name', '')).toBe('{{ brand.name }}');
		expect(fieldToken('amount', null)).toBe('{{ amount }}');
	});

	it('pipes a filter in', () => {
		expect(fieldToken('amount', 'currency')).toBe('{{ amount | currency }}');
	});

	it('answers empty for no field, rather than a token that renders nothing', () => {
		expect(fieldToken('')).toBe('');
		expect(fieldToken('   ', 'currency')).toBe('');
	});

	it('trims, so a stray space cannot produce a broken token', () => {
		expect(fieldToken('  amount  ', '  currency  ')).toBe('{{ amount | currency }}');
	});
});

describe('parseFieldToken', () => {
	it('round-trips what fieldToken writes', () => {
		expect(parseFieldToken('{{ amount | currency }}')).toEqual({
			field: 'amount',
			filter: 'currency',
			fallback: ''
		});
		expect(parseFieldToken('{{ first_name }}')).toEqual({
			field: 'first_name',
			filter: '',
			fallback: ''
		});
	});

	it('reads a token written by hand, whatever the spacing', () => {
		expect(parseFieldToken('{{amount|currency}}')).toEqual({
			field: 'amount',
			filter: 'currency',
			fallback: ''
		});
	});

	it('refuses a chain rather than shortening it', () => {
		// Valid Jinja this panel can't represent: reporting null leaves the author's text alone rather than silently dropping a filter.
		expect(parseFieldToken('{{ x | lower | truncate }}')).toBeNull();
	});

	it('refuses a filter with an argument, for the same reason', () => {
		expect(parseFieldToken('{{ amount | currency("$") }}')).toBeNull();
	});

	it('refuses anything that is not a token', () => {
		expect(parseFieldToken('{% if x %}')).toBeNull();
		expect(parseFieldToken('plain words')).toBeNull();
		expect(parseFieldToken('{{ }}')).toBeNull();
		expect(parseFieldToken('')).toBeNull();
		expect(parseFieldToken(null)).toBeNull();
		expect(parseFieldToken(undefined)).toBeNull();
	});
});

describe('describeFilter', () => {
	it('names the no-filter case, so the control never reads blank', () => {
		expect(describeFilter('')).toBe('No formatting');
		expect(describeFilter(null)).toBe('No formatting');
	});

	it('answers the unset option in the exported words, so the menu and the summary agree', () => {
		// The Format menu offers `NO_FILTER`/`NO_FILTER_LABEL` and this describes what a field carries;
		// a control holding its own copy of either is how the two start saying different things about the
		// same state.
		expect(NO_FILTER).toBe('');
		expect(describeFilter(NO_FILTER)).toBe(NO_FILTER_LABEL);
	});

	it('falls back to the raw name for a filter it does not offer', () => {
		expect(describeFilter('upper')).toBe('upper');
	});
});

describe('the fallback — what to show when there is nothing', () => {
	it('is registered on the send side, like every other filter offered', () => {
		const engine = readFileSync(ENGINE, 'utf8');
		expect(engine).toContain(`env.filters["${FALLBACK_FILTER}"]`);
	});

	it('is absent from the offered FORMATS — it is a different question', () => {
		// A different question from Format ("how is this rendered" vs "what if there is nothing"); offered in FIELD_FILTERS it would render with no argument and look like the control did nothing.
		expect(FIELD_FILTERS.map((f) => f.value)).not.toContain(FALLBACK_FILTER);
	});

	it('goes LAST, after the format', () => {
		// Fallback goes after the format so `currency` can turn 0 into "£0.00" first — a real zero, left alone rather than replaced.
		expect(fieldToken('amount', 'currency', '—')).toBe("{{ amount | currency | fallback('—') }}");
	});

	it('emits nothing when there is no fallback, so existing tokens are untouched', () => {
		expect(fieldToken('first_name')).toBe('{{ first_name }}');
		expect(fieldToken('first_name', '')).toBe('{{ first_name }}');
		expect(fieldToken('first_name', '', '')).toBe('{{ first_name }}');
		expect(fieldToken('amount', 'currency')).toBe('{{ amount | currency }}');
	});

	it('round-trips', () => {
		const cases: [string, string, string][] = [
			['first_name', '', 'there'],
			['amount', 'currency', '—'],
			['due_date', 'date', 'no date set'],
			['first_name', '', ' '] // a single space keeps a sentence's spacing — a real answer
		];
		for (const [field, filter, fallback] of cases) {
			const parsed = parseFieldToken(fieldToken(field, filter, fallback));
			expect(parsed).toEqual({ field, filter, fallback });
		}
	});

	it('survives a quote, a backslash and a pipe in the fallback', () => {
		for (const text of [
			"your adviser's team",
			String.raw`a\b`,
			'either|or',
			String.raw`it's \ both`
		]) {
			const parsed = parseFieldToken(fieldToken('sent_by', '', text));
			expect(parsed?.fallback).toBe(text);
			expect(parsed?.field).toBe('sent_by');
		}
	});

	it('reads a token that has no fallback as having none, not as unparseable', () => {
		expect(parseFieldToken('{{ first_name }}')).toEqual({
			field: 'first_name',
			filter: '',
			fallback: ''
		});
		expect(parseFieldToken('{{ amount | currency }}')).toEqual({
			field: 'amount',
			filter: 'currency',
			fallback: ''
		});
	});

	it('refuses the two the wrong way round, rather than half-reading it', () => {
		expect(parseFieldToken("{{ amount | fallback('—') | currency }}")).toBeNull();
	});

	it('refuses a second fallback and a hand-written chain', () => {
		expect(parseFieldToken("{{ x | fallback('a') | fallback('b') }}")).toBeNull();
		expect(parseFieldToken('{{ x | lower | truncate }}')).toBeNull();
		expect(parseFieldToken("{{ x | date('%Y') }}")).toBeNull();
	});

	it('says what an empty fallback means, because that is the case worth naming', () => {
		expect(describeFallback('')).toMatch(/nothing/i);
		expect(describeFallback(null)).toMatch(/nothing/i);
		expect(describeFallback('there')).toMatch(/0 is not/i);
	});
});
