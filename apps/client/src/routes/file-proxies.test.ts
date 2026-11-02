/**
 * The five same-origin file proxies: bounded buffering and a validated id in the upstream path.
 *
 * Each used to `res.arrayBuffer()` whatever the API sent — pinning that much memory per request
 * with no ceiling — and spliced its route param into the API URL raw. They all relay through
 * `@graphene/auth`'s `relayBuffered` now (stops at the API's own 25 MiB object ceiling) and refuse
 * an id that is not a public id before anything is fetched.
 */
import { describe, expect, it, vi } from 'vitest';
import { GET as documentContent } from './documents/[uid]/content/+server';
import { GET as fileDownload } from './files/[uid]/download/+server';
import { GET as attachment } from './messages/attachments/[attachmentUid]/+server';
import { GET as requestItem } from './api/request-items/[req_uid]/[item_uid]/content/+server';
import { GET as guide } from './insights/[uid]/file/+server';

vi.mock('$lib/api', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/api')>();
	return { ...real, apiUrl: () => 'http://api.test' };
});
vi.mock('$lib/server/workos', () => ({ ACTIVE_CONTACT_COOKIE: 'gp-active-contact-client' }));

type Handler = (event: unknown) => Promise<Response>;

function upstream(bytes: number, headers: Record<string, string> = {}) {
	return vi.fn(
		async () =>
			new Response(new Uint8Array(bytes), {
				status: 200,
				headers: { 'content-type': 'application/pdf', ...headers }
			})
	);
}

async function call(
	handler: Handler,
	params: Record<string, string>,
	fetch: ReturnType<typeof vi.fn>
) {
	try {
		return await handler({
			params,
			locals: { user: { id: 'u1' }, accessToken: 'tok' },
			fetch,
			cookies: { get: () => undefined },
			url: new URL('http://portal.test/x')
		});
	} catch (e) {
		// SvelteKit's `error()` throws an HttpError.
		const status = (e as { status?: number }).status;
		if (typeof status === 'number') return new Response(null, { status });
		throw e;
	}
}

const proxies: [string, Handler, Record<string, string>][] = [
	['documents/[uid]/content', documentContent as unknown as Handler, { uid: 'doc123456789' }],
	['files/[uid]/download', fileDownload as unknown as Handler, { uid: 'fil123456789' }],
	[
		'messages/attachments/[attachmentUid]',
		attachment as unknown as Handler,
		{ attachmentUid: 'att123456789' }
	],
	[
		'api/request-items/…/content',
		requestItem as unknown as Handler,
		{ req_uid: 'req123456789', item_uid: 'itm123456789' }
	],
	['insights/[uid]/file', guide as unknown as Handler, { uid: 'gde123456789' }]
];

describe.each(proxies)('%s', (_name, handler, params) => {
	it('relays an in-cap body with the upstream type and an explicit length', async () => {
		const res = await call(handler, params, upstream(1024));
		expect(res.status).toBe(200);
		expect(res.headers.get('content-type')).toBe('application/pdf');
		expect(res.headers.get('content-length')).toBe('1024');
		expect(res.headers.get('cache-control')).toBe('private, no-store');
	});

	it('refuses an over-cap body by its declared length, without buffering it', async () => {
		const fetch = upstream(16, { 'content-length': String(30 * 1024 * 1024) });
		const res = await call(handler, params, fetch);
		expect(res.status).toBe(502);
	});

	it('refuses an id that could leave its path segment before anything is fetched', async () => {
		const fetch = upstream(16);
		const bad = Object.fromEntries(Object.keys(params).map((k) => [k, '../admin?x=1']));
		const res = await call(handler, bad, fetch);
		expect(res.status).toBe(404);
		expect(fetch).not.toHaveBeenCalled();
	});
});
