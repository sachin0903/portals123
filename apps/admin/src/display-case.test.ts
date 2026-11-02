/**
 * A value is printed in the words the product uses for it — across all three portals and the
 * shared package, on the source.
 *
 * WHY A STATIC SCAN. Nothing else can see this. `class="capitalize"` type-checks, lints and
 * renders; every unit test asserting on the markup passes, because the value in the DOM is the raw
 * key and the Title-Casing happens in the browser's layout. The only reader who ever sees it is a
 * person looking at two badges in one row — which is how it was reported: "Owner vs draft",
 * "blocked lowercase".
 *
 * The rule and its reasoning are `./display-case.ts`, beside `route-addressing.ts` for the same
 * reason: one rule, one copy, three portals.
 */
import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import {
	capitalizeOffences,
	rawStatusBadges,
	RAW_STATUS_ALLOWED,
	stripComments,
	svelteFiles,
	CAPITALIZE_TOKEN
} from './display-case';

describe('a value is named, not CSS-capitalised', () => {
	it('finds what it is meant to be scanning', () => {
		// The guard on the guard: a workspace rearrangement that leaves this scanning nothing must
		// fail loudly rather than pass vacuously.
		const files = svelteFiles();
		expect(files.length).toBeGreaterThan(200);
		for (const root of ['../client/src', '../platform/src', '../admin/src', '../../pkgs/ui/src'])
			expect(files.some((f) => f.startsWith(root))).toBe(true);
	});

	it('no portal reaches for `capitalize` to make an enum presentable', () => {
		expect(capitalizeOffences().map((o) => `${o.file}:${o.line}  ${o.text}`)).toEqual([]);
	});

	it('no badge prints the API\u2019s key as its label', () => {
		// The other half of the same rule. `capitalize` was the Title-Casing habit; this is the
		// lowercase one — a pill rendering `{x.status}` straight from the API, which is how one
		// screen came to show "completed" beside another showing "Completed".
		expect(rawStatusBadges().map((o) => `${o.file}:${o.line}  ${o.text}`)).toEqual([]);
	});

	it('the documented exceptions are still the ones being excepted', () => {
		// A stale allowlist silently re-permits the thing it was written to allow ONCE. Each entry
		// names a file that must still exist and still hold that expression.
		for (const key of Object.keys(RAW_STATUS_ALLOWED)) {
			const at = key.lastIndexOf(':');
			const [file, expr] = [key.slice(0, at), key.slice(at + 1)];
			expect(existsSync(file), `${file} is allowlisted but gone`).toBe(true);
			expect(readFileSync(file, 'utf8'), `${file} no longer renders {${expr}}`).toContain(
				`{${expr}}`
			);
		}
	});

	it('ignores the attribute and the comments that explain the rule', () => {
		// `autocapitalize` is a real attribute on the email and code-view inputs, and four modules
		// document this rule by quoting the banned string.
		expect(CAPITALIZE_TOKEN.test('autocapitalize="none"')).toBe(false);
		expect(CAPITALIZE_TOKEN.test('class="capitalize"')).toBe(true);
		expect(stripComments('<!-- class="capitalize" is banned -->\n<p>ok</p>')).not.toContain(
			'capitalize'
		);
		expect(stripComments('// `class="capitalize"` Title-Cases it\nconst a = 1;')).not.toContain(
			'capitalize'
		);
	});
});
