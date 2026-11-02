import { describe, expect, it } from 'vitest';
import { RESERVED_SLUGS, SHADOWABLE_SLUGS } from '@graphene/ui/portal-slugs';
import { match } from './portalPage';

// SvelteKit explicitly allows *.test.ts collocation in src/params (create_matchers skips
// them), so this lives beside the matcher it pins.

describe('portalPage param matcher', () => {
	it('accepts well-formed custom page slugs', () => {
		for (const slug of ['reports', 'my-page', 'q2-2026', 'a', 'x'.repeat(32)]) {
			expect(match(slug), slug).toBe(true);
		}
	});

	it('rejects every reserved slug — real routes must 404 as page slugs', () => {
		for (const slug of RESERVED_SLUGS) {
			expect(match(slug), slug).toBe(false);
		}
	});

	it('accepts the shadowable slugs (their static routes outrank the param route anyway)', () => {
		for (const slug of SHADOWABLE_SLUGS) {
			expect(match(slug), slug).toBe(true);
		}
	});

	it('rejects anything outside the canonical slug shape', () => {
		for (const slug of ['', 'Has-Caps', 'under_score', 'café', 'a b', 'x'.repeat(33), 'a/b']) {
			expect(match(slug), JSON.stringify(slug)).toBe(false);
		}
	});
});
