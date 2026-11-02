/**
 * The client request inbox's fulfil action.
 *
 * Pins the bug this shipped: a chosen file of zero bytes was filtered out before the outgoing body was
 * built, so the API saw an upload item with NO file and answered "This action requires a file upload"
 * — which reads as "you didn't choose anything" to a client who did. The dud file has to reach the API
 * so the API can name what's actually wrong with it.
 */

import { describe, expect, it, vi } from 'vitest';
import { actions, load } from './+page.server';

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

type Handler = (url: string, init: RequestInit | undefined) => Response | Promise<Response>;

function harness(handler: Handler) {
	const calls: { url: string; init: RequestInit | undefined }[] = [];
	const fetch = vi.fn(async (input: unknown, init?: RequestInit) => {
		const url = String(input);
		calls.push({ url, init });
		return handler(url, init);
	});
	return { fetch, calls };
}

function fulfilEvent(fetch: ReturnType<typeof harness>['fetch'], file?: File) {
	const body = new FormData();
	body.append('req_uid', 'req_1');
	body.append('item_uid', 'rqi_1');
	if (file) body.append('file', file, file.name);
	return {
		request: new Request('http://portal.test/requests?/fulfilItem', { method: 'POST', body }),
		locals: { user: { id: 'u1' }, accessToken: 'tok' },
		fetch
	};
}

type ActionArg = Parameters<typeof actions.fulfilItem>[0];
type LoadArg = Parameters<typeof load>[0];

/** The `file` part of the multipart body the action posted, if it sent one. */
const postedFile = (init: RequestInit | undefined): File | null => {
	const part = (init?.body as FormData).get('file');
	return part instanceof File ? part : null;
};

describe('client requests — fulfilItem', () => {
	it('refuses an id that could leave its path segment before anything is fetched', async () => {
		// `${api}/documents/requests/${reqUid}/items/${itemUid}/fulfill` with a traversal in a form
		// field retargeted the POST (still carrying the caller's bearer) at whatever route the
		// caller named. Both ids go through `publicId`; a bad one is a 400 and no upstream call.
		const { fetch, calls } = harness(() => json({}));
		const body = new FormData();
		body.append('req_uid', 'x/../../messaging/thread/messages?');
		body.append('item_uid', 'rqi_1');
		const result = await actions.fulfilItem({
			request: new Request('http://portal.test/requests?/fulfilItem', { method: 'POST', body }),
			locals: { user: { id: 'u1' }, accessToken: 'tok' },
			fetch
		} as unknown as ActionArg);
		expect(result).toMatchObject({ status: 400 });
		expect(calls).toHaveLength(0);
	});

	it('forwards the chosen file', async () => {
		const { fetch, calls } = harness(() => json({ uid: 'req_1', items: [] }));
		const file = new File([new Uint8Array([1, 2, 3])], 'statement.pdf');
		const result = await actions.fulfilItem(fulfilEvent(fetch, file) as unknown as ActionArg);
		expect(result).toMatchObject({ ok: true });
		expect(calls[0].url).toContain('/documents/requests/req_1/items/rqi_1/fulfill');
		expect(postedFile(calls[0].init)?.name).toBe('statement.pdf');
	});

	it('forwards a zero-byte file so the API names the real problem', async () => {
		const { fetch, calls } = harness(() =>
			json({ detail: 'document must be 1..26214400 bytes' }, 422)
		);
		const result = await actions.fulfilItem(
			fulfilEvent(fetch, new File([], 'empty.pdf')) as unknown as ActionArg
		);
		expect(postedFile(calls[0].init)?.name).toBe('empty.pdf');
		expect(result).toMatchObject({ status: 422 });
		expect((result as { data: { error: string } }).data.error).toContain('1..26214400 bytes');
	});

	it('omits a nameless empty part (an unfilled file input, not a file)', async () => {
		const { fetch, calls } = harness(() =>
			json({ detail: 'This action requires a file upload.' }, 422)
		);
		await actions.fulfilItem(fulfilEvent(fetch, new File([], '')) as unknown as ActionArg);
		expect(postedFile(calls[0].init)).toBeNull();
	});

	it('sends no file at all for a confirm-only item', async () => {
		const { fetch, calls } = harness(() => json({ uid: 'req_1', items: [] }));
		await actions.fulfilItem(fulfilEvent(fetch) as unknown as ActionArg);
		expect(postedFile(calls[0].init)).toBeNull();
	});

	it('refuses an unidentified item, and an anonymous caller, without touching the API', async () => {
		const noItem = harness(() => json({}));
		const bare = new FormData();
		bare.append('req_uid', 'req_1');
		expect(
			await actions.fulfilItem({
				request: new Request('http://portal.test/requests?/fulfilItem', {
					method: 'POST',
					body: bare
				}),
				locals: { user: { id: 'u1' }, accessToken: 'tok' },
				fetch: noItem.fetch
			} as unknown as ActionArg)
		).toMatchObject({ status: 400 });
		expect(noItem.calls).toEqual([]);

		const anon = harness(() => json({}));
		expect(
			await actions.fulfilItem({
				...(fulfilEvent(anon.fetch) as object),
				locals: { user: null, accessToken: null }
			} as unknown as ActionArg)
		).toMatchObject({ status: 401 });
		expect(anon.calls).toEqual([]);
	});
});

describe('client requests — targetDoc', () => {
	/** Answers `/terms` and `/target` from the two responses given. */
	const twoStep = (terms: Response, target: Response) =>
		harness((url) => (url.endsWith('/terms') ? terms : target));

	const askTarget = (fetch: ReturnType<typeof harness>['fetch']) => {
		const body = new FormData();
		body.append('req_uid', 'req_1');
		body.append('item_uid', 'rqi_1');
		return {
			request: new Request('http://portal.test/requests?/targetDoc', { method: 'POST', body }),
			locals: { user: { id: 'u1' }, accessToken: 'tok' },
			fetch
		} as unknown as Parameters<typeof actions.targetDoc>[0];
	};

	it('serves a PDF from our own origin, never the presigned URL that previewed blank', async () => {
		const { fetch } = twoStep(
			json({ detail: 'Terms not found' }, 404),
			json({ name: 'Mandate.pdf', content_type: 'application/pdf' })
		);
		expect(await actions.targetDoc(askTarget(fetch))).toMatchObject({
			ok: true,
			content_type: 'application/pdf',
			url: '/api/request-items/req_1/rqi_1/content'
		});
	});

	it('falls through to the stored file when a terms row carries no HTML', async () => {
		const { fetch } = twoStep(
			json({ name: 'Terms', content: null }),
			json({ name: 'Terms.pdf', content_type: 'application/pdf' })
		);
		expect(await actions.targetDoc(askTarget(fetch))).toMatchObject({
			url: '/api/request-items/req_1/rqi_1/content'
		});
	});
});

describe('client requests — load', () => {
	it('hides cancelled requests and flags a failed fetch instead of an empty inbox', async () => {
		const failing = harness(() => json({ detail: 'nope' }, 403));
		const failed = (await load({
			locals: { user: { id: 'u1' }, accessToken: 'tok' },
			fetch: failing.fetch,
			parent: async () => ({ activeContactId: null }),
			depends: () => {}
		} as unknown as LoadArg)) as { requests: unknown[]; loadError: boolean };
		expect(failed.loadError).toBe(true);
		expect(failed.requests).toEqual([]);

		const listed = harness(() =>
			json([
				{ uid: 'req_live', status: 'pending', items: [] },
				{ uid: 'req_dead', status: 'cancelled', items: [] }
			])
		);
		const data = (await load({
			locals: { user: { id: 'u1' }, accessToken: 'tok' },
			fetch: listed.fetch,
			parent: async () => ({ activeContactId: null }),
			depends: () => {}
		} as unknown as LoadArg)) as { requests: { uid: string }[]; loadError: boolean };
		expect(data.requests.map((r) => r.uid)).toEqual(['req_live']);
		expect(data.loadError).toBe(false);
	});

	it('asks only for the active clients outstanding requests', async () => {
		// The composed pages' own action-items slice already narrowed this endpoint; the standalone
		// page did not, so it listed one client's requests while the nav named another.
		const listed = harness(() => json([]));
		await load({
			locals: { user: { id: 'u1' }, accessToken: 'tok' },
			fetch: listed.fetch,
			parent: async () => ({ activeContactId: 4 }),
			depends: () => {}
		} as unknown as LoadArg);
		expect(listed.calls[0].url).toContain('contact_id=4');
	});
});
