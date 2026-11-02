import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const layouts = [
	new URL('./+layout.svelte', import.meta.url),
	new URL('../../../client/src/routes/+layout.svelte', import.meta.url),
	new URL('../../../platform/src/routes/+layout.svelte', import.meta.url)
];

describe('auth-page shell ownership', () => {
	it('uses the rendered route in every portal, not a destination still loading', () => {
		for (const layout of layouts) {
			const source = readFileSync(layout, 'utf8');
			const expression = source.match(/const isAuthRoute =[^;]+;/s)?.[0] ?? '';

			expect(expression).toContain('page.url.pathname');
			expect(expression).not.toContain('pendingHref');
		}
	});
});
