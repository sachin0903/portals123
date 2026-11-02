import { readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { RESERVED_SLUGS, SHADOWABLE_SLUGS } from '@graphene/ui/portal-slugs';

/**
 * Every top-level route in this app must be known to the portal-page slug contract
 * (pkgs/ui block-contract.json): RESERVED (a real route a firm page may never take) or
 * SHADOWABLE (a default page a firm may replace). The failure mode this trips on: someone adds
 * a new top-level route, the reserved list doesn't learn about it, and a firm then names a page
 * after it — the branding API accepts the slug and the static route silently swallows the
 * firm's page (or worse, a later refactor lets the page shadow a real surface). Add the new
 * route to the contract's reservedSlugs (both sides: block-contract.json + app/branding.py's
 * validation reads the same file) in the same change that adds the route.
 */
describe('top-level routes vs the portal slug contract', () => {
	it('every top-level route directory is a reserved or shadowable slug', () => {
		// Resolved from this file's URL (not cwd) so the test cannot silently scan nothing when
		// vitest runs from somewhere unexpected.
		const routesDir = fileURLToPath(new URL('../routes', import.meta.url));
		const dirs = readdirSync(routesDir, { withFileTypes: true })
			.filter((entry) => entry.isDirectory())
			.map((entry) => entry.name)
			// The composed catch-all is the one route that exists to serve NON-reserved slugs.
			.filter((name) => name !== '[slug=portalPage]');

		// A guard that silently matches nothing is not a guard.
		expect(dirs.length).toBeGreaterThan(5);

		const known = new Set([...RESERVED_SLUGS, ...SHADOWABLE_SLUGS]);
		const offenders = dirs.filter((dir) => !known.has(dir));
		expect(
			offenders,
			'Top-level route directories the slug contract does not know about. Add each to ' +
				"reservedSlugs in pkgs/ui's block-contract.json (the shared Python/TS contract) in this " +
				'same change, or a firm can create a portal page whose slug collides with the route.'
		).toEqual([]);
	});
});
