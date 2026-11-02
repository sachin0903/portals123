import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { PORTAL_SLUG_RE, RESERVED_SLUGS, SHADOWABLE_SLUGS } from './portal-slugs.js';
import * as portalPages from './portal-pages.js';
import { slugify } from './portal-pages.js';

// Read the contract independently of the module under test (registry.test.ts convention), so a
// broken import or stale copy fails here rather than at a firm's first save.
const contract = JSON.parse(
	readFileSync(new URL('./block-contract.json', import.meta.url), 'utf8')
) as { reservedSlugs: string[]; shadowableSlugs: string[] };

describe('portal-slugs pins to block-contract.json', () => {
	it('RESERVED_SLUGS equals the contract list', () => {
		expect([...RESERVED_SLUGS].sort()).toEqual([...contract.reservedSlugs].sort());
	});

	it('SHADOWABLE_SLUGS equals the contract list', () => {
		expect([...SHADOWABLE_SLUGS].sort()).toEqual([...contract.shadowableSlugs].sort());
	});

	it('portal-pages re-exports the very same objects (one source of truth)', () => {
		expect(portalPages.RESERVED_SLUGS).toBe(RESERVED_SLUGS);
		expect(portalPages.SHADOWABLE_SLUGS).toBe(SHADOWABLE_SLUGS);
		expect(portalPages.PORTAL_SLUG_RE).toBe(PORTAL_SLUG_RE);
	});
});

describe('PORTAL_SLUG_RE', () => {
	const valid = ['fees', 'my-page-2', '0', 'a', 'x'.repeat(32), ...contract.shadowableSlugs];
	for (const s of valid)
		it(`accepts '${s}'`, () => {
			expect(PORTAL_SLUG_RE.test(s)).toBe(true);
		});

	const invalid: [string, string][] = [
		['', 'empty'],
		['x'.repeat(33), 'over the 32-char cap'],
		['Fees', 'uppercase'],
		['my page', 'whitespace'],
		['my_page', 'underscore'],
		['café', 'non-ascii'],
		['/fees', 'path separator'],
		['fees?x=1', 'query junk']
	];
	for (const [s, why] of invalid)
		it(`rejects '${s}' (${why})`, () => {
			expect(PORTAL_SLUG_RE.test(s)).toBe(false);
		});

	it('accepts every slug slugify() can emit (the two rules cannot drift)', () => {
		const labels = [
			'Fee Schedule',
			'  Émojis 🎉 & Symbols!! ',
			'',
			'🎉🎉🎉',
			'Onboarding', // reserved ⇒ '-2' suffixed
			'The Quarterly Portfolio Performance Review',
			'x'.repeat(100)
		];
		for (const label of labels) expect(slugify(label)).toMatch(PORTAL_SLUG_RE);
	});
});
