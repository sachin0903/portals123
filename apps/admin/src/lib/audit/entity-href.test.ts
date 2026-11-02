/**
 * Every entity chip the trail can link must point at a page this portal actually has.
 *
 * Asserted against the ROUTE TREE ON DISK, the way the platform portal's own copy is: a link to a
 * route that does not exist is worse than no link, because the chip invites the click and the 404
 * reads as the record having been deleted — which is exactly the misreading the September 2026
 * event-content review set out to remove.
 *
 * Both families are checked, because they are not interchangeable: OPERATE has a page for nearly
 * every kind the trail resolves, BOOK has two, and offering a book link for a document would 404.
 */
import { describe, expect, it } from 'vitest';
import { existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { bookEntityHref, operateEntityHref } from './entity-href';

const ROUTES_DIR = join(process.cwd(), 'src', 'routes');
const TENANT = 'ten_abc';
const UID = 'u1';

/** Does this route path exist on disk? A segment equal to the uid matches ANY `[param]` directory —
 *  admin names its params for the record (`[journeyUid]`), so the spelling is not fixed. */
function routeExists(path: string): boolean {
	let dir = ROUTES_DIR;
	for (const segment of path.split('/').filter(Boolean)) {
		if (segment === TENANT || segment === UID) {
			const param = readdirSync(dir, { withFileTypes: true }).find(
				(e) => e.isDirectory() && e.name.startsWith('[') && e.name.endsWith(']')
			);
			if (!param) return false;
			dir = join(dir, param.name);
			continue;
		}
		dir = join(dir, segment);
		if (!existsSync(dir)) return false;
	}
	return existsSync(dir);
}

const OPERATE_KINDS = [
	'contact',
	'account',
	'document',
	'task',
	'template',
	'form',
	'flow',
	'journey',
	'content',
	'automation',
	'dataset',
	'document_request'
];

describe('operateEntityHref', () => {
	it('routes exactly the kinds this file claims to', () => {
		const routed = OPERATE_KINDS.filter((kind) =>
			operateEntityHref(TENANT, { kind, id: UID, uid: UID, label: 'x' })
		);
		expect(routed).toEqual(OPERATE_KINDS);
	});

	it.each(OPERATE_KINDS)('%s points at a page that exists', (kind) => {
		const href = operateEntityHref(TENANT, { kind, id: UID, uid: UID, label: 'x' });
		expect(href).toBeTruthy();
		expect(routeExists(href as string)).toBe(true);
	});

	it('links nothing without a uid', () => {
		expect(operateEntityHref(TENANT, { kind: 'contact', id: '42', uid: null, label: 'x' })).toBe(
			null
		);
	});
});

describe('bookEntityHref', () => {
	it.each(['contact', 'account'])('%s points at a page that exists', (kind) => {
		const href = bookEntityHref(TENANT, { kind, id: UID, uid: UID, label: 'x' });
		expect(href).toBeTruthy();
		expect(routeExists(href as string)).toBe(true);
	});

	it('offers nothing beyond the two records book can show', () => {
		// The read-only oversight view has a record page for a client and an account and nothing
		// else — a document, a journey or an automation would 404, so no chip there invites a click.
		for (const kind of ['document', 'task', 'journey', 'automation', 'content']) {
			expect(bookEntityHref(TENANT, { kind, id: UID, uid: UID, label: 'x' })).toBe(null);
		}
	});
});
