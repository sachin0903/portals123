import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
	MERGE_FIELD_GROUPS,
	MERGE_FIELDS,
	NON_INSERTABLE_KEYS,
	SCOPED_CODES,
	firstMatch,
	labelForField,
	matchFields,
	mergeFieldForPath,
	mergeFieldsFor
} from './merge-fields';

const tokensOf = (groups: { fields: { token: string }[] }[]) =>
	new Set(groups.flatMap((g) => g.fields.map((f) => f.token)));

describe('email merge-field catalogue', () => {
	it('flattens every named group into MERGE_FIELDS, with no duplicate tokens', () => {
		expect(MERGE_FIELDS).toEqual(MERGE_FIELD_GROUPS.flatMap((g) => g.fields));
		const tokens = MERGE_FIELDS.map((f) => f.token);
		expect(new Set(tokens).size).toBe(tokens.length);
		for (const g of MERGE_FIELD_GROUPS) {
			expect(g.group.trim().length).toBeGreaterThan(0);
			expect(g.fields.length).toBeGreaterThan(0);
		}
		for (const f of MERGE_FIELDS) {
			expect(f.label.trim().length).toBeGreaterThan(0);
			expect(f.token).toMatch(/^\{\{ [a-z_.]+ \}\}$/);
		}
	});

	it('uses the flat send-site vocabulary, never a nested spelling', () => {
		// The nested spellings render blank at a real send.
		const tokens = new Set(MERGE_FIELDS.map((f) => f.token));
		for (const real of [
			'first_name',
			'brand.name',
			'document_name',
			'set_password_url',
			'due_date'
		])
			expect(tokens).toContain(`{{ ${real} }}`);
		for (const wrong of ['client.first_name', 'request.items', 'document.name', 'adviser.name'])
			expect(tokens).not.toContain(`{{ ${wrong} }}`);
	});
});

describe('mergeFieldsFor (per-code scoping)', () => {
	it('offers each code only its own send site’s tokens', () => {
		const invite = tokensOf(mergeFieldsFor('invite'));
		expect(invite).toContain('{{ set_password_url }}');
		expect(invite).not.toContain('{{ document_name }}');
		expect(invite).not.toContain('{{ message }}');

		const sent = tokensOf(mergeFieldsFor('document_sent'));
		expect(sent).toContain('{{ sent_by }}');
		expect(sent).toContain('{{ portal_url }}');
		expect(sent).not.toContain('{{ set_password_url }}');

		// The client-facing document codes both carry the covering note.
		for (const code of ['document_sent', 'request_raised'])
			expect(tokensOf(mergeFieldsFor(code))).toContain('{{ message }}');
	});

	it('offers each task code the notifier’s tokens, not the document/invite vocabulary', () => {
		// The notifier resolves no recipient name and sends no document, so those tokens would render blank in every real send — asserted over both codes because a vocabulary fixed on one and forgotten on the other is exactly the drift two near-identical codes invite.
		for (const code of ['task_assigned', 'task_reminder']) {
			const tokens = tokensOf(mergeFieldsFor(code));
			for (const has of ['task_title', 'task_description', 'due_date', 'button_url'])
				expect(tokens, code).toContain(`{{ ${has} }}`);
			for (const not of ['first_name', 'document_name', 'set_password_url'])
				expect(tokens, code).not.toContain(`{{ ${not} }}`);
			// Neither template states its kind through a token or a condition — each says it in its own words, and the notifier passes no flag to branch on.
			expect(tokens, code).not.toContain('{{ notice_kind }}');
			expect(tokens, code).not.toContain('{{ is_reminder }}');
		}
		expect([...tokensOf(mergeFieldsFor('task_assigned'))].sort()).toEqual(
			[...tokensOf(mergeFieldsFor('task_reminder'))].sort()
		);
	});

	it('offers a custom or unknown code the UNIVERSAL fields alone', () => {
		// No send site passes the whole catalogue, so offering it to a code we cannot identify offers
		// mostly blanks. The firm's own fields go on every send; the rest is declared as a variable.
		for (const code of ['quarterly_statement', null]) {
			const groups = mergeFieldsFor(code);
			expect(groups.map((g) => g.group)).toEqual(['General']);
			expect(tokensOf(groups)).toEqual(new Set(['{{ brand.name }}', '{{ brand.support_email }}']));
		}
	});
});

describe('mergeFieldForPath — the resolver over every DECLARED field', () => {
	it('resolves the notification wrapper’s own two slots, which the menu vocabulary omits', () => {
		// `{{ title }}` and `{{ body }}` are the notification layout's slots: declared, offered by the
		// `notification` code, and deliberately absent from MERGE_FIELD_GROUPS (the reference vocabulary
		// the parity tests iterate, not a menu). A resolver built off the flattened groups answers
		// nothing for them, and a caller that drops what it cannot resolve then offers a custom template
		// every notification key EXCEPT the two the wrapper exists to place.
		const offered = new Set(MERGE_FIELDS.map((f) => f.token));
		for (const path of ['title', 'body']) {
			expect(offered, path).not.toContain(`{{ ${path} }}`);
			const field = mergeFieldForPath(path);
			expect(field, path).not.toBeNull();
			expect(field?.token, path).toBe(`{{ ${path} }}`);
			// A row with no label and no example is worse than no row: the panel would show a bare token.
			expect(field?.label.trim().length, path).toBeGreaterThan(0);
			expect(field?.example, path).toBeTruthy();
		}
	});

	it('resolves every field every scoped code offers, so no menu row can be unresolvable', () => {
		for (const code of SCOPED_CODES)
			for (const field of mergeFieldsFor(code).flatMap((g) => g.fields)) {
				const path = field.token.replace(/^\{\{ | \}\}$/g, '');
				expect(mergeFieldForPath(path), `${code}.${path}`).not.toBeNull();
			}
	});

	it('answers a nested path as well as a flat one, since the key is not the token', () => {
		// `brand_name` carries `{{ brand.name }}`: keyed by the KEY this resolves nothing.
		expect(mergeFieldForPath('brand.name')?.token).toBe('{{ brand.name }}');
		expect(mergeFieldForPath('brand_name')).toBeNull();
	});

	it('resolves a shared path to the SAMPLED entry, never the deliberately-blank one', () => {
		// The three names are declared twice — once sampled, once blank for `password_reset`, whose rows
		// state the blank because naming the recipient makes that email an account-existence oracle.
		// A resolver reached without a code cannot know which send is asking, so it answers the value the
		// token normally carries; `mergeFieldsFor('password_reset')` still hands out the blank rows.
		for (const path of ['first_name', 'full_name', 'surname'])
			expect(mergeFieldForPath(path)?.example, path).toBeTruthy();
		expect(
			mergeFieldsFor('password_reset')
				.flatMap((g) => g.fields)
				.find((f) => f.token === '{{ first_name }}')?.example
		).toBeNull();
	});

	it('answers null for a path nothing declares, and tolerates the empties', () => {
		expect(mergeFieldForPath('quarterly_return')).toBeNull();
		expect(mergeFieldForPath('  first_name  ')?.token).toBe('{{ first_name }}');
		expect(mergeFieldForPath('')).toBeNull();
		expect(mergeFieldForPath(null)).toBeNull();
	});

	it('is what labelForField names a canvas chip from', () => {
		// One derivation off `T`, so a chip's badge and a menu row cannot disagree about a field's words.
		expect(labelForField('title')).toBe(mergeFieldForPath('title')?.label);
		expect(labelForField('brand.name')).toBe('Firm name');
		// A path the catalogue does not know has no label, and the canvas falls back to the path itself.
		expect(labelForField('quarterly_return')).toBeNull();
	});
});

// Mirrors Python's `app/email/preview.py::_CODE_KEYS`: the Insert menu must offer exactly what each send site actually supplies, read straight from the Python source since there's no shared JSON — brittle by construction, hence the non-vacuity checks below.
describe('parity with the API’s per-code send-site contract', () => {
	// fileURLToPath, not `.pathname` — on Windows the latter yields `/C:/…`, which `join` mangles into `C:\C:\…`, and the read would throw rather than explain itself.
	const source = readFileSync(
		join(
			fileURLToPath(new URL('../../../../../../', import.meta.url)),
			'apps/api/app/email/preview.py'
		),
		'utf8'
	);

	const dictBody = (name: string, text: string = source): string =>
		text.match(new RegExp(`${name}[^=]*= \\{([\\s\\S]*?)\\n\\}`))?.[1] ?? '';

	const pythonCodeKeys = (): Record<string, string[]> => {
		const out: Record<string, string[]> = {};
		for (const [, code, list] of dictBody('_CODE_KEYS').matchAll(/"(\w+)":\s*\[([\s\S]*?)\]/g))
			out[code] = [...list.matchAll(/"(\w+)"/g)].map((k) => k[1]);
		return out;
	};

	// `email_defaults._SUBJECT_TOKENS` is a THIRD category — tokens a code derives rather than receives, in neither `_CODE_KEYS` nor `NON_INSERTABLE_KEYS` but still offered since they appear in the shipped subject and must stay editable.
	const derivedSource = readFileSync(
		join(
			fileURLToPath(new URL('../../../../../../', import.meta.url)),
			'apps/api/app/email/defaults.py'
		),
		'utf8'
	);

	const pythonDerivedTokens = (): Record<string, string[]> => {
		const out: Record<string, string[]> = {};
		// TUPLES, not lists — `("request_summary",)` — so the value pattern differs from `pythonCodeKeys` above; the dict-finding half is shared.
		for (const [, code, tuple] of dictBody('_SUBJECT_TOKENS', derivedSource).matchAll(
			/"(\w+)":\s*\(([^)]*)\)/g
		))
			out[code] = [...tuple.matchAll(/"(\w+)"/g)].map((k) => k[1]);
		return out;
	};

	const pythonSampleKeys = (): Set<string> =>
		new Set([...dictBody('_SAMPLE').matchAll(/^\s{4}"(\w+)":/gm)].map((m) => m[1]));

	/** key → the RAW Python literal, so a computed sample (`due_date`) and a LIST (`item_titles`) can be
	 *  told apart from a plain string rather than all three compared as one. */
	const pythonSampleLiterals = (): Record<string, string> =>
		Object.fromEntries(
			[...dictBody('_SAMPLE').matchAll(/^\s{4}"(\w+)":\s*(.+?),\s*(?:#.*)?$/gm)].map((m) => [
				m[1],
				m[2]
			])
		);

	const sampleString = (literal: string): string | null =>
		literal.match(/^"([^"]*)"$/)?.[1] ?? null;

	/** `due_date` is COMPUTED, so the expectation is derived from the same two numbers — pinning the date
	 *  string would fail the day the reference date moves, which is not drift. */
	const pythonComputedDate = (literal: string): string | null => {
		const today = source.match(/_TODAY = date\((\d+), (\d+), (\d+)\)/);
		const days = literal.match(/^\(_TODAY \+ timedelta\(days=(\d+)\)\)\.isoformat\(\)$/);
		if (!today || !days) return null;
		return new Date(Date.UTC(+today[1], +today[2] - 1, +today[3] + +days[1]))
			.toISOString()
			.slice(0, 10);
	};

	/** `_CODE_SAMPLE_OVERRIDES`: code → key → raw literal (`None` for a send that carries no value). */
	const pythonSampleOverrides = (): Record<string, Record<string, string>> =>
		Object.fromEntries(
			[...dictBody('_CODE_SAMPLE_OVERRIDES').matchAll(/"(\w+)":\s*\{([^}]*)\}/g)].map(
				([, code, inner]) => [
					code,
					Object.fromEntries(
						[...inner.matchAll(/"(\w+)":\s*(None|"[^"]*")/g)].map((m) => [m[1], m[2]])
					)
				]
			)
		);

	const bare = (token: string) => token.replace(/^\{\{ | \}\}$/g, '');

	const exampleFor = (code: string, key: string): string | null | undefined =>
		mergeFieldsFor(code)
			.flatMap((g) => g.fields)
			.find((f) => bare(f.token) === key)?.example;

	it('offers exactly each code’s send-site keys, minus the deliberate exclusions', () => {
		for (const [code, keys] of Object.entries(pythonCodeKeys())) {
			const derived = pythonDerivedTokens();
			const expected = [
				...keys.filter((k) => !NON_INSERTABLE_KEYS.includes(k)),
				...(derived[code] ?? [])
			].sort();
			const offered = [...tokensOf(mergeFieldsFor(code))]
				.map(bare)
				// `brand.*` is universal (passed on every send) and lives outside the flat key list.
				.filter((t) => !t.startsWith('brand.'))
				.sort();
			expect(offered, `merge fields for '${code}' have drifted from _CODE_KEYS`).toEqual(expected);
		}
	});

	it('scopes exactly the codes the API scopes — neither side knows one the other does not', () => {
		// A code the API has and the editor lacks falls through to the permissive full set; a code the editor has and the API lacks offers tokens against a send site that doesn't exist — splitting or renaming a code trips both at once.
		expect([...SCOPED_CODES].sort()).toEqual(Object.keys(pythonCodeKeys()).sort());
	});

	it('every token names a real sample key, so none can render blank in preview', () => {
		const sample = pythonSampleKeys();
		expect(
			MERGE_FIELDS.map((f) => bare(f.token))
				.filter((t) => !t.startsWith('brand.'))
				.filter((t) => !sample.has(t))
		).toEqual([]);
	});

	it('shows the value Preview renders as every offered field’s example', () => {
		// An example is the whole point of the reference panel: `{{ portal_label }}` names nothing on its
		// own, and one that disagrees with Preview is worse than none.
		const literals = pythonSampleLiterals();
		const overrides = pythonSampleOverrides();
		const unsampled = new Set<string>();
		for (const code of SCOPED_CODES) {
			for (const field of mergeFieldsFor(code).flatMap((g) => g.fields)) {
				const key = bare(field.token);
				const where = `${code}.${key}`;
				// `brand.*` is the firm's REAL resolved brand, outside `_SAMPLE` — illustrative, but present.
				if (key.startsWith('brand.')) {
					expect(field.example, where).toBeTruthy();
					continue;
				}
				if (overrides[code]?.[key] === 'None') {
					expect(field.example, where).toBeNull();
					continue;
				}
				const literal = literals[key];
				if (literal === undefined) {
					unsampled.add(key);
					expect(field.example, where).toBeTruthy();
					continue;
				}
				expect(field.example, where).toBe(sampleString(literal) ?? pythonComputedDate(literal));
			}
		}
		// The only offerable key with no sample: DERIVED by the send (`derived_tokens`), not passed to it.
		expect([...unsampled]).toEqual(['request_summary']);
	});

	it('never carries a LIST sample as an example, which would print a Python array', () => {
		const lists = Object.entries(pythonSampleLiterals())
			.filter(([, literal]) => literal.startsWith('['))
			.map(([key]) => key);
		expect(lists).toEqual(['item_titles']);
		const offered = new Set(
			SCOPED_CODES.flatMap((c) => [...tokensOf(mergeFieldsFor(c))].map(bare))
		);
		for (const key of lists) {
			expect(NON_INSERTABLE_KEYS).toContain(key);
			expect(offered).not.toContain(key);
		}
	});

	it('scopes a deliberately-blank example to the code that earns it', () => {
		// `password_reset` carries no first name — naming the recipient makes the email an
		// account-existence oracle — so its row must state the blank while every other code samples it.
		const overrides = pythonSampleOverrides();
		expect(Object.values(overrides).flatMap((o) => Object.keys(o)).length).toBeGreaterThan(0);
		const literals = pythonSampleLiterals();
		for (const [code, keys] of Object.entries(overrides)) {
			for (const [key, literal] of Object.entries(keys)) {
				if (literal !== 'None') continue;
				expect(exampleFor(code, key), `${code}.${key}`).toBeNull();
				for (const other of SCOPED_CODES.filter((c) => c !== code)) {
					const example = exampleFor(other, key);
					if (example !== undefined)
						expect(example, `${other}.${key}`).toBe(sampleString(literals[key]));
				}
			}
		}
	});

	it('parses the Python source, so the assertions above cannot pass vacuously', () => {
		expect(source.length).toBeGreaterThan(0);
		// The EXACT count, not a floor. These regexes are indentation-exact, so the realistic failure is a
		// PARTIAL parse — one code's entry reformatted just enough to stop matching — and every assertion
		// above iterates the parsed dict, so a code that drops out of it is a code nobody compares: the
		// suite goes on passing while that code's whole vocabulary is unchecked. A floor cannot tell six
		// codes from seven. When the API genuinely adds one, this number moves with the parity above it.
		expect(Object.keys(pythonCodeKeys()).length).toBe(7);
		expect(pythonSampleKeys().size).toBeGreaterThan(10);
		// The exclusions must still BE send-site keys — one renamed backend-side excludes nothing, and one no longer passed leaves this list describing a vocabulary that no longer has it.
		const allKeys = new Set(Object.values(pythonCodeKeys()).flat());
		for (const k of NON_INSERTABLE_KEYS) expect(allKeys).toContain(k);
		expect(Object.keys(pythonDerivedTokens()).length).toBeGreaterThan(0);
		// Every sample key must reach the LITERAL parser too, or the example assertions skip fields.
		expect(new Set(Object.keys(pythonSampleLiterals()))).toEqual(pythonSampleKeys());
		expect(Object.keys(pythonSampleOverrides()).length).toBeGreaterThan(0);
	});
});

describe('matchFields — the one matcher behind the flyout and the canvas `{{` menu', () => {
	const groups = MERGE_FIELD_GROUPS;

	it('returns every group untouched for a blank query', () => {
		expect(matchFields(groups, '').map((g) => g.group)).toEqual(groups.map((g) => g.group));
		expect(matchFields(groups, '   ')).toHaveLength(groups.length);
		expect(matchFields(groups, null)).toHaveLength(groups.length);
	});

	it('matches a label as a SUBSTRING, not a prefix', () => {
		// "name" is the obvious thing to type for "First name", and a prefix rule finds nothing.
		const hit = matchFields(groups, 'name').flatMap((g) => g.fields.map((f) => f.label));
		expect(hit).toContain('First name');
		expect(hit).toContain('Firm name');
	});

	it('matches the token, so an author who knows the Jinja finds it', () => {
		const hit = matchFields(groups, 'set_password').flatMap((g) => g.fields.map((f) => f.label));
		expect(hit).toEqual(['Set-password link']);
	});

	it('matches the group, because that is how some fields are remembered', () => {
		const hit = matchFields(groups, 'task');
		expect(hit.map((g) => g.group)).toContain('Task');
		expect(hit.flatMap((g) => g.fields).length).toBeGreaterThan(1);
	});

	it('is case-insensitive', () => {
		expect(matchFields(groups, 'PORTAL')).toEqual(matchFields(groups, 'portal'));
	});

	it('drops groups with no match rather than showing an empty heading', () => {
		for (const group of matchFields(groups, 'portal')) {
			expect(group.fields.length).toBeGreaterThan(0);
		}
	});

	it('answers nothing for a query that matches nothing', () => {
		expect(matchFields(groups, 'zzzz')).toEqual([]);
		expect(firstMatch(groups, 'zzzz')).toBeNull();
	});

	it('firstMatch is the first row of the filtered list — what Enter must pick', () => {
		const filtered = matchFields(groups, 'portal');
		expect(firstMatch(groups, 'portal')).toEqual(filtered[0]?.fields[0]);
	});
});
