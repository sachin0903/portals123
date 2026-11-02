/**
 * `GET /aml/checks/[uid]/images/[part]` — one photo of an identity check, streamed through this
 * app so the browser never talks to the API or ComplyCube directly. Mirrors
 * `documents/[uid]/content`.
 */
import { describe, expect, it, vi } from 'vitest';
import { GET } from './+server';

const locals = (permissions: string[] = ['aml:read']) => ({
	user: { id: 'u-ops' } as { id: string } | null,
	accessToken: 'tok',
	permissions,
	role: null
});

function call(
	part: string,
	{
		upstream = new Response('bytes', { headers: { 'content-type': 'image/jpeg' } }),
		who = locals()
	} = {}
) {
	const fetch = vi.fn(async () => upstream);
	const result = GET({ params: { uid: 'chk_1', part }, locals: who, fetch } as never);
	return { fetch, result };
}

describe('GET /aml/checks/[uid]/images/[part]', () => {
	it('streams the bytes and headers the API sent', async () => {
		const upstream = new Response('%PDF', {
			headers: {
				'content-type': 'application/pdf',
				'content-disposition': 'inline; filename="front.pdf"',
				'x-content-type-options': 'nosniff'
			}
		});
		const { fetch, result } = call('front', { upstream });
		const res = (await result) as Response;
		expect(await res.text()).toBe('%PDF');
		expect(res.headers.get('content-type')).toBe('application/pdf');
		expect(res.headers.get('content-disposition')).toBe('inline; filename="front.pdf"');
		expect(res.headers.get('cache-control')).toBe('private, no-store');
		expect(res.headers.get('x-content-type-options')).toBe('nosniff');
		const [url] = fetch.mock.calls[0] as unknown as [string];
		expect(url).toBe('http://127.0.0.1:8000/aml/checks/chk_1/images/front');
	});

	it('defaults content-disposition to inline and the cache to private, no-store', async () => {
		const { result } = call('selfie', {
			upstream: new Response('bytes', { headers: { 'content-type': 'image/png' } })
		});
		const res = (await result) as Response;
		expect(res.headers.get('content-disposition')).toBe('inline');
		expect(res.headers.get('cache-control')).toBe('private, no-store');
	});

	it('404s on a part the relay does not know, without calling the API', async () => {
		const { fetch, result } = call('front-and-back');
		await expect(result).rejects.toMatchObject({ status: 404 });
		expect(fetch).not.toHaveBeenCalled();
	});

	it('needs aml:read and a signed-in user', async () => {
		const denied = call('front', { who: locals([]) });
		await expect(denied.result).rejects.toMatchObject({ status: 403 });
		expect(denied.fetch).not.toHaveBeenCalled();

		const anonymous = call('front', { who: { ...locals(), user: null } });
		await expect(anonymous.result).rejects.toMatchObject({ status: 303, location: '/login' });
		expect(anonymous.fetch).not.toHaveBeenCalled();
	});

	it('relays a 404/409 from the API as itself', async () => {
		const { result } = call('front', {
			upstream: new Response(
				JSON.stringify({ detail: 'This identity check is not held for review.' }),
				{
					status: 409
				}
			)
		});
		await expect(result).rejects.toMatchObject({
			status: 409,
			body: { message: 'This identity check is not held for review.' }
		});
	});

	it('maps any other API failure to 502', async () => {
		const { result } = call('front', { upstream: new Response(null, { status: 500 }) });
		await expect(result).rejects.toMatchObject({ status: 502 });
	});
});
