/**
 * The client-nested document record.
 *
 * `/clients/{uid}/documents/{uid}` renders the same record as `/documents/{uid}` — same loader,
 * same actions — with one thing the flat route could not do: refuse a document that is not this
 * client's. Without that, hand-editing the client segment in the URL renders someone else's
 * document under this client's breadcrumb. It is not a leak (documents:read is firm-wide, and RLS bounds
 * the tenant either way); it is a URL that lies about what it is showing.
 */

import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

const apiDoc = {
	uid: 'doc_1',
	name: 'Suitability report',
	document_type: 'suitability',
	source: 'generated',
	status: 'generated',
	content_type: 'application/pdf',
	size_bytes: 2048,
	created_at: '2026-07-01T09:00:00Z',
	created_by: 'Ada Adviser',
	client: 'Bo Client',
	client_contact_id: null,
	client_viewed: false,
	client_viewed_at: null
};

/** `live`/`archived` are what `GET /documents?contact_id=8` answers with for each slice; `status`
    lets a test make one of those reads fail. */
function harness({
	live = [apiDoc],
	archived = [] as unknown[],
	listStatus = 200
}: { live?: unknown[]; archived?: unknown[]; listStatus?: number } = {}) {
	const fetch = vi.fn(async (input: unknown) => {
		const url = String(input);
		if (url.includes('status=archived')) return json(archived, listStatus);
		if (url.includes('contact_id=')) return json(live, listStatus);
		if (url.endsWith('/documents/doc_1')) return json(apiDoc);
		if (url.includes('/firm/audit/actions')) return json({ items: [] });
		if (url.includes('/requests')) return json([]);
		return json([]);
	});
	return fetch;
}

const event = (fetch: ReturnType<typeof harness>) =>
	({
		params: { uid: 'con_8b4f', doc: 'doc_1' },
		locals: { user: { id: 'u1' }, permissions: ['documents:read'], role: null, accessToken: 't' },
		fetch,
		// The client is named in the URL by uid; the SERIAL the documents API filters on comes from
		// the record the client layout already loaded, which is what `parent()` publishes.
		parent: async () => ({ detail: { contact: { id: 8, uid: 'con_8b4f' } } })
	}) as never;

/** The loader's type includes `void` because it can `redirect`; none of these cases does. */
async function loaded(fetch: ReturnType<typeof harness>) {
	const data = await load(event(fetch));
	if (!data) throw new Error('the loader redirected, which no case here should');
	return { ...data, ...(await data.record) };
}

describe('the client-nested document record', () => {
	it('loads a document filed under this client', async () => {
		const data = await loaded(harness());
		expect(data.doc?.uid).toBe('doc_1');
	});

	it('loads one that is archived under this client', async () => {
		// Archived documents are reachable from the client's Archived view, so checking only the
		// live slice would 404 a link the page itself renders.
		const data = await loaded(harness({ live: [], archived: [apiDoc] }));
		expect(data.doc?.uid).toBe('doc_1');
	});

	it('404s a document that is not this client’s', async () => {
		const data = await load(event(harness({ live: [], archived: [] })));
		if (!data) throw new Error('the loader redirected, which no case here should');
		await expect(data.record).rejects.toMatchObject({ status: 404 });
	});

	it('does NOT 404 when the membership read itself fails', async () => {
		// A flaky list call must not be reported as "this document does not exist" — that turns a
		// transient error into a dead link on a page the user reached correctly.
		const data = await loaded(harness({ listStatus: 503 }));
		expect(data.doc?.uid).toBe('doc_1');
	});

	it('keeps the content stream on the flat path', async () => {
		// The preview/download proxy is a resource endpoint, not a page — nesting it would mean a
		// second copy of `content/+server.ts`.
		const data = await loaded(harness());
		expect(data.previewUrl).toBe('/documents/doc_1/content');
	});

	it('skips the membership reads entirely on the flat route', async () => {
		// The library's detail passes no contactId, so it must not pay for two list calls.
		const { load: flatLoad } = await import('../../../../documents/[uid]/+page.server');
		const fetch = harness();
		const loaded = await flatLoad({
			params: { uid: 'doc_1' },
			locals: { user: { id: 'u1' }, permissions: ['documents:read'], role: null, accessToken: 't' },
			fetch
		} as never);
		if (loaded && 'record' in loaded) await loaded.record;
		expect(fetch.mock.calls.some(([u]) => String(u).includes('contact_id='))).toBe(false);
	});
});
