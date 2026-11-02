import { globSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

/**
 * SvelteKit validates a route module's exports at REQUEST time, not build time — so an illegal
 * export passes `svelte-check`, `eslint`, `vitest` and `vite build`, then 500s every request to
 * that route with "Invalid export 'x'". That is exactly how `basisByAccount` shipped and took out
 * every `/clients/[id]` page load while the whole gate stayed green.
 *
 * The escape hatch SvelteKit sanctions is an underscore prefix, which is what a helper that only
 * exists to be unit-tested should carry. This test is the gate the framework doesn't give us.
 *
 * Mirrored in the other two portals — each app scans its own routes.
 */
const ALLOWED = new Set([
	// +page.server.ts / +layout.server.ts
	'load',
	'actions',
	'prerender',
	'csr',
	'ssr',
	'trailingSlash',
	'config',
	'entries',
	// +server.ts
	'GET',
	'POST',
	'PUT',
	'PATCH',
	'DELETE',
	'OPTIONS',
	'HEAD',
	'fallback'
]);

const EXPORTED = /^export\s+(?:async\s+)?(?:const|let|var|function|class)\s+(\w+)/gm;

describe('route module exports', () => {
	it('exports nothing SvelteKit would reject at request time', () => {
		const files = [
			...globSync('src/routes/**/+*.server.ts'),
			...globSync('src/routes/**/+server.ts')
		];
		// A guard that silently matches nothing is not a guard.
		expect(files.length).toBeGreaterThan(0);

		const offenders = files.flatMap((file) => {
			const source = readFileSync(file, 'utf8');
			return [...source.matchAll(EXPORTED)]
				.map((m) => m[1])
				.filter((name) => !ALLOWED.has(name) && !name.startsWith('_'))
				.map((name) => `${file} exports '${name}' — rename to '_${name}'`);
		});

		expect(offenders).toEqual([]);
	});
});

describe('signed-out redirects', () => {
	it("send a page load to login WITH the place the reader was heading, never a bare '/login'", () => {
		// `loginPath(url)` carries `?next=` so the sign-in lands back where the session expired.
		// Twenty-odd loads each spelled `redirect(303, '/login')` and lost it; the MFA flow's own
		// redirects are the sanctioned exception — there is no page to return to from there.
		// Separators normalised: on Windows `globSync` answers with `\`, and the exclusion missed.
		const files = globSync('src/routes/**/+*.server.ts').filter(
			(f) => !f.replaceAll('\\', '/').includes('/mfa/')
		);
		const bare = files.filter((file) =>
			/redirect\(303,\s*'\/login'\)/.test(readFileSync(file, 'utf8'))
		);
		expect(bare).toEqual([]);
	});
});
