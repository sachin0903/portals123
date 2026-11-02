import { describe, it, expect } from 'vitest';
import {
	unsourcedTokens,
	datasetVariableGroups,
	collectionOptions,
	BUILTIN_COLLECTIONS
} from './variables';

describe('unsourcedTokens', () => {
	it('passes common context + form fields, flags only hand-typed tokens', () => {
		const html =
			'<p>{{ client.name }} — {{ adviser.name }} at {{ firm.name }} on {{ today }}</p>' +
			'<p>Fee: {{ fee_pct }} · Investor: {{ investor_name }} · {{ deductions_2yr_35 }}</p>';
		// fee_pct + investor_name are form fields; the rest of the first line is common context.
		expect(unsourcedTokens(html, ['fee_pct', 'investor_name'])).toEqual(['deductions_2yr_35']);
	});

	it('treats loop sources and loop-bound vars as sourced (datasets)', () => {
		const html =
			'{% for row in client_holdings %}<tr><td>{{ row.product }}</td>' +
			'<td>{{ row.value }}</td></tr>{% endfor %}';
		// `client_holdings` (loop source) and `row` (loop target) are both legitimate.
		expect(unsourcedTokens(html, [])).toEqual([]);
	});

	it('flags a mistyped loop-bound access only when the root is unknown', () => {
		// `rows` is neither a loop target nor common/form/dataset → flagged; `row` (bound) is fine.
		const html = '{% for row in holdings %}{{ row.x }} {{ rows.y }}{% endfor %}';
		expect(unsourcedTokens(html, [])).toEqual(['rows']);
	});

	it('ignores datasets/one map access and Jinja builtins', () => {
		const html = '{{ datasets.fees }} {{ one.fees.rate }} {{ loop.index }} {{ range(3) }}';
		expect(unsourcedTokens(html, [])).toEqual([]);
	});

	it('de-dupes and preserves first-seen order', () => {
		const html = '{{ foo }} {{ bar }} {{ foo }}';
		expect(unsourcedTokens(html, [])).toEqual(['foo', 'bar']);
	});

	it('is empty for no content or no tokens', () => {
		expect(unsourcedTokens('', [])).toEqual([]);
		expect(unsourcedTokens('<p>plain text</p>', [])).toEqual([]);
	});
});

describe('unsourcedTokens — dataset column awareness (#8)', () => {
	const cols = { holdings: ['product', 'value'] };

	it('flags a mistyped column on a dataset-bound loop var when the columns are known', () => {
		const html = '{% for row in holdings %}{{ row.product }} {{ row.typo }}{% endfor %}';
		expect(unsourcedTokens(html, [], cols)).toEqual(['row.typo']);
	});

	it('does NOT flag columns when the dataset columns are unknown or empty', () => {
		const html = '{% for row in holdings %}{{ row.typo }}{% endfor %}';
		expect(unsourcedTokens(html, [], {})).toEqual([]);
		expect(unsourcedTokens(html, [], { holdings: [] })).toEqual([]);
	});

	it('does not column-check a loop over a non-dataset source (columns unknown → silent)', () => {
		const html = '{% for row in mystery %}{{ row.x }}{% endfor %}';
		expect(unsourcedTokens(html, [], cols)).toEqual([]);
	});

	it('does not column-check multi-target (destructuring) loops', () => {
		const html = '{% for k, row in mapping %}{{ row.typo }}{% endfor %}';
		expect(unsourcedTokens(html, [], cols)).toEqual([]);
	});

	it('still flags an unknown root even alongside dataset column checks', () => {
		const html = '{% for row in holdings %}{{ mystery_fee }} {{ row.typo }}{% endfor %}';
		expect(unsourcedTokens(html, [], cols)).toEqual(['mystery_fee', 'row.typo']);
	});

	it('de-dupes a repeated bad column', () => {
		const html = '{% for row in holdings %}{{ row.typo }} {{ row.typo }}{% endfor %}';
		expect(unsourcedTokens(html, [], cols)).toEqual(['row.typo']);
	});

	it('flags a mistyped column on a single-value one.<code>.<col> binding', () => {
		expect(unsourcedTokens('{{ one.holdings.valeu }}', [], cols)).toEqual(['one.holdings.valeu']);
	});

	it('passes a valid one.* binding and stays soft for unknown/empty-column datasets', () => {
		expect(unsourcedTokens('{{ one.holdings.value }}', [], cols)).toEqual([]);
		expect(unsourcedTokens('{{ one.mystery.value }}', [], cols)).toEqual([]); // unknown code → soft
		expect(unsourcedTokens('{{ one.holdings.value }}', [], {})).toEqual([]); // no column info → soft
	});
});

describe('datasetVariableGroups', () => {
	const opts = [
		{ code: 'holdings', name: 'Fund Holdings', columns: ['product', 'value'] },
		{ code: 'no_schema', name: 'No Schema', columns: [] }
	];

	it('emits value + row groups per dataset with known columns (skipping schema-less ones)', () => {
		expect(datasetVariableGroups(opts)).toEqual([
			{
				group: 'Fund Holdings — single value',
				vars: [
					{ key: 'one.holdings.product', label: 'product' },
					{ key: 'one.holdings.value', label: 'value' }
				]
			},
			{
				group: 'Fund Holdings — row field (inside a repeat loop)',
				vars: [
					{ key: 'row.product', label: 'product' },
					{ key: 'row.value', label: 'value' }
				]
			}
		]);
	});

	it('skips datasets with no known columns — never guesses', () => {
		expect(datasetVariableGroups([{ code: 'x', name: 'X', columns: [] }])).toEqual([]);
	});

	it('falls back to the code as the group label when the name is blank', () => {
		expect(datasetVariableGroups([{ code: 'holdings', name: '', columns: ['x'] }])).toEqual([
			{ group: 'holdings — single value', vars: [{ key: 'one.holdings.x', label: 'x' }] },
			{
				group: 'holdings — row field (inside a repeat loop)',
				vars: [{ key: 'row.x', label: 'x' }]
			}
		]);
	});

	it('is empty for no datasets', () => {
		expect(datasetVariableGroups([])).toEqual([]);
	});
});

describe('the client’s own book', () => {
	it('accepts accounts and portfolio without a dataset behind them', () => {
		const html =
			'<p>{{ portfolio.account_count }} accounts, {{ portfolio.total_value | currency }}</p>' +
			'{% for row in accounts %}<p>{{ row.name }}: {{ row.value | currency }}</p>{% endfor %}';
		expect(unsourcedTokens(html)).toEqual([]);
	});

	it('column-checks a loop over accounts, so a mistyped field is caught', () => {
		// The whole point of declaring the built-in's columns: `row.valu` renders blank in a client's
		// document and nothing else would report it.
		const cols = Object.fromEntries(BUILTIN_COLLECTIONS.map((c) => [c.code, c.columns]));
		const html = '{% for row in accounts %}<p>{{ row.valu }}</p>{% endfor %}';
		expect(unsourcedTokens(html, [], cols)).toEqual(['row.valu']);
	});
});

describe('collectionOptions', () => {
	const DS = { code: 'holdings', name: 'Fund Holdings', columns: ['product', 'value'] };

	it('offers the built-ins first, then the firm’s datasets', () => {
		expect(collectionOptions([DS]).map((c) => c.code)).toEqual(['accounts', 'holdings']);
	});

	it('lets a firm’s own dataset take a built-in code, and does not offer both', () => {
		// Two entries with the same code would put two identically-valued items in every picker, and
		// `.find` would resolve the columns of whichever came first — so the firm’s wins outright.
		const own = { code: 'accounts', name: 'Our accounts extract', columns: ['acct_no'] };
		const out = collectionOptions([own]);
		expect(out.map((c) => c.code)).toEqual(['accounts']);
		expect(out[0]).toEqual({
			code: 'accounts',
			name: 'Our accounts extract',
			columns: ['acct_no']
		});
	});

	it('is just the built-ins when the firm has no datasets', () => {
		expect(collectionOptions([]).map((c) => c.code)).toEqual(['accounts']);
	});

	it('declares a column for every account field the picker offers', () => {
		// The list is mirrored in two Python places (`render/sample.py::_SAMPLE_ACCOUNTS` and
		// `worker.py::_client_accounts_context`) and cannot be imported from either, so this pins the
		// TS half against drift: a field dropped here silently stops being offered and stops being
		// column-checked.
		expect(BUILTIN_COLLECTIONS.find((c) => c.code === 'accounts')?.columns).toEqual([
			'name',
			'number',
			'product',
			'product_type',
			'status',
			'value',
			'currency',
			'risk_level',
			'model',
			'fee_package',
			'opened',
			'value_as_of'
		]);
	});
});
