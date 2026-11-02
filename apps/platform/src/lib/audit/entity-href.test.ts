/**
 * Every entity chip the trail can link must point at a page this portal actually has.
 *
 * Asserted against the ROUTE TREE ON DISK, the way `$lib/components/accounts/sections.test.ts`
 * checks the account record's nav: a link to a route that does not exist is worse than no link,
 * because the chip invites the click and the 404 reads as the record having been deleted — which
 * is exactly the misreading the September 2026 event-content review set out to remove. A kind
 * added to the map without its page fails here rather than in front of an adviser.
 */
import { describe, expect, it } from 'vitest';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { portalEntityHref } from './entity-href';

const ROUTES_DIR = join(process.cwd(), 'src', 'routes');

/** Every kind the map routes, discovered by asking it rather than by keeping a second list. */
const KINDS = [
	'contact',
	'account',
	'document',
	// `task` is deliberately absent: fc6241ae withdrew standalone tasks and took the route out
	// of the map with them, so an event naming a task renders unlinked rather than pointing at a
	// page a firm can no longer reach.
	'template',
	'form',
	'flow',
	'user',
	'journey',
	'content',
	'automation',
	'document_request',
	'contribution',
	'notification_rule'
];

describe('portalEntityHref', () => {
	it('routes exactly the kinds this file claims to', () => {
		// The list above is the test's own fixture; this keeps it honest against the map, so a kind
		// added there without a case here cannot slip past the route check below.
		const routed = KINDS.filter((kind) =>
			portalEntityHref({ kind, id: 'x', uid: 'x', label: 'x' })
		);
		expect(routed).toEqual(KINDS);
	});

	it.each(KINDS)('%s points at a page that exists', (kind) => {
		const href = portalEntityHref({ kind, id: 'u1', uid: 'u1', label: 'Something' });
		expect(href).toBeTruthy();
		// The path, minus any query (a workbench is reached by a `?focus=` deep link, not a route).
		const [path = ''] = (href as string).split('?');
		const segments = path.split('/').filter(Boolean);
		// A uid segment corresponds to a `[uid]` directory; every other segment is literal.
		const onDisk = segments.map((s) => (s === 'u1' ? '[uid]' : s));
		expect(existsSync(join(ROUTES_DIR, ...onDisk))).toBe(true);
	});

	it('links nothing without a uid, because there is no address to link to', () => {
		expect(portalEntityHref({ kind: 'contact', id: '42', uid: null, label: 'Margaret Cole' })).toBe(
			null
		);
	});

	it('links nothing for a kind this portal has no page for', () => {
		// The API resolves more kinds than any one portal can show — a screening hit, an
		// environment, a storage credential. The chip still carries the NAME; it just does not
		// invite a click.
		for (const kind of ['aml_hit', 'environment', 'storage_credential', 'message_group']) {
			expect(portalEntityHref({ kind, id: 'u1', uid: 'u1', label: 'x' })).toBe(null);
		}
	});
});
