/**
 * The Deleted documents listing: the read it makes, how it fails, and the vocabulary it renders.
 *
 * The vocabulary scan is the load-bearing one. The DB/API word is `archived` and the users' word is
 * DELETED, and the two must not cross: the query parameter has to stay `archived` (the enum is not
 * being renamed) while nothing on screen may say it, because the platform portal is being relabelled
 * to Deleted and one surface disagreeing with the other is how an operator ends up telling a firm
 * their documents are in two different states. Both halves are asserted against the real source.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { load } from './+page.server';
import { documentName, typeLabel } from '$lib/book/deleted-documents';
import { formatBytes, formatDate } from '@graphene/ui';

const TENANT = { id: 4, uid: 't_9f2ab1', name: 'Caldwell Wealth' };
const locals = {
	user: { id: 'u-ops' },
	accessToken: 'tok',
	permissions: ['book:read'],
	role: null
};
const parent = async () => ({ tenant: TENANT });

/** The page URL the loader reads its query out of — the listing is server-driven now. */
const pageUrl = (search = '') =>
	new URL(`http://ops/tenants/${TENANT.uid}/book/documents${search}`);

function run(body: unknown, status = 200, search = '') {
	const calls: URL[] = [];
	const fetch = (async (url: string | URL) => {
		const u = new URL(String(url));
		calls.push(u);
		return new Response(JSON.stringify(body), { status });
	}) as unknown as typeof globalThis.fetch;
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	return { calls, load: () => (load as any)({ locals, fetch, parent, url: pageUrl(search) }) };
}

describe('the deleted documents loader', () => {
	it('reads the ARCHIVED slice through the book target', async () => {
		const { calls, load } = run({
			items: [{ uid: 'doc_1', name: 'Letter', status: 'archived' }],
			total: 1,
			facets: {}
		});
		const result = await load();
		expect(calls[0].pathname).toBe('/documents');
		// The wire contract stays the API's word — this is the one place `archived` belongs.
		expect(calls[0].searchParams.get('status')).toBe('archived');
		// Without ?tenant= the API resolves the OPERATIONS tenant's own empty book and the page
		// would quietly render nothing — the failure `bookUrl` exists to make impossible.
		expect(calls[0].searchParams.get('tenant')).toBe(TENANT.uid);
		// A limit is what opts into the paged envelope; without one the API answers a bare list
		// and the page loses its total, its facets and its pager.
		expect(calls[0].searchParams.get('limit')).toBeTruthy();
		expect(result.documents).toHaveLength(1);
		expect(result.total).toBe(1);
		expect(result.loadError).toBe(false);
	});

	it('still reads a BARE list, which the API answers when no limit is asked for', async () => {
		// `.items` off an array is `undefined`, and an empty table on THIS page reads as "this firm
		// has deleted nothing" — the one answer it must never give by accident.
		const result = await run([{ uid: 'doc_1', name: 'Letter', status: 'archived' }]).load();
		expect(result.documents).toHaveLength(1);
		expect(result.total).toBe(1);
	});

	it('carries the listing filters through to the API, and nothing else', async () => {
		const { calls, load } = run(
			{ items: [], total: 0, facets: {} },
			200,
			'?q=letter&type=suitability_report&focus=doc_9'
		);
		await load();
		expect(calls[0].searchParams.get('q')).toBe('letter');
		expect(calls[0].searchParams.get('type')).toBe('suitability_report');
		// `focus` is not one of this listing's filters, so it must not reach the API — the whole
		// reason `queryFromParams` is given a key list rather than reading every param it sees.
		expect(calls[0].searchParams.get('focus')).toBeNull();
	});

	it('flags a failed read instead of collapsing it to an empty list', async () => {
		// The distinction the page's banner turns on: "nothing deleted" and "we could not tell you"
		// look identical in an empty table, and only one of them is safe to show an operator.
		const result = await run({ detail: 'boom' }, 500).load();
		expect(result.documents).toEqual([]);
		expect(result.loadError).toBe(true);
	});

	it('sends a signed-out visitor to /login before fetching anything', async () => {
		const calls: URL[] = [];
		const fetch = (async (url: string | URL) => {
			calls.push(new URL(String(url)));
			return new Response('[]');
		}) as unknown as typeof globalThis.fetch;
		await expect(
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			(load as any)({ locals: { ...locals, user: null }, fetch, parent })
		).rejects.toMatchObject({ status: 303, location: '/login' });
		expect(calls).toEqual([]);
	});
});

describe('the deleted/archived vocabulary split', () => {
	const page = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');
	const loader = readFileSync(new URL('./+page.server.ts', import.meta.url), 'utf8');

	it('says "Deleted" to the user', () => {
		expect(page).toContain('Deleted documents');
	});

	it('never renders the word "archived" — only the query parameter uses it', () => {
		// Strip comments and the <script> block: the explanatory prose legitimately discusses the
		// enum, and it is the RENDERED markup that must not contradict the platform portal. (This
		// is the same scoped-scan trade `switch-columns.test.ts` records making.)
		const markup = page
			.replace(/<script[\s\S]*?<\/script>/g, '')
			.replace(/<!--[\s\S]*?-->/g, '')
			.replace(/\{#if[\s\S]*?\}/g, '');
		expect(markup.toLowerCase()).not.toContain('archiv');
		// And the parameter itself is not in the markup either — it belongs to the loader, which
		// sets it on top of the listing's own params (it selects the deleted SLICE rather than
		// being one of the filters a reader can change).
		expect(loader).toContain("params.set('status', 'archived')");
	});

	it('is read-only: no form actions and no $app/forms anywhere in this route', () => {
		// Restore is deliberately absent — `POST /documents/{uid}/unarchive` runs on tenant_session
		// and takes no `?tenant=`, so offering it here would mean widening the ops proxy WRITE
		// surface past the client record, which `book.test.ts` pins. This keeps that decision
		// visible instead of drifting back in.
		expect(loader).not.toMatch(/export\s+const\s+actions/);
		expect(page).not.toContain('$app/forms');
	});
});

describe('the listing formatters', () => {
	it('renders a size, and an ABSENT size as a dash rather than zero', () => {
		expect(formatBytes(512)).toBe('512 B');
		expect(formatBytes(2048)).toBe('2 KB');
		expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB');
		expect(formatBytes(40 * 1024 * 1024)).toBe('40.0 MB');
		expect(formatBytes(2 * 1024 * 1024 * 1024)).toBe('2.0 GB');
		// The point: no stored file is not a file of nought bytes.
		expect(formatBytes(null)).toBe('—');
		expect(formatBytes(undefined)).toBe('—');
		expect(formatBytes(0)).toBe('0 B');
	});

	it('renders a British short date, and never "Invalid Date"', () => {
		expect(formatDate('2026-08-14T09:30:00Z')).toBe('14 Aug 2026');
		expect(formatDate('not a date')).toBe('—');
		expect(formatDate(null)).toBe('—');
	});

	it('falls back to the uid for an unnamed document', () => {
		expect(documentName({ name: 'Suitability report', uid: 'doc_1' })).toBe('Suitability report');
		// A blank cell would hide a row an operator may need to quote in a ticket.
		expect(documentName({ name: '   ', uid: 'doc_1' })).toBe('doc_1');
		expect(documentName({ name: null, uid: 'doc_1' })).toBe('doc_1');
	});

	it('de-slugs a type in sentence case (the slugs are free text a firm coins)', () => {
		expect(typeLabel('suitability_report')).toBe('Suitability report');
		expect(typeLabel('proof-of-address')).toBe('Proof of address');
		expect(typeLabel('')).toBe('—');
	});
});
