/**
 * `GET /clients/[uid]/compliance/identity/[check]/[part]` — one photo of a client's identity
 * check, streamed through this app so the browser never talks to the API directly. Mirrors
 * `documents/[uid]/content`.
 */
import { describe, expect, it, vi } from 'vitest';
import { GET } from './+server';

const locals = (permissions: string[] = ['screening:read']) => ({
	user: { id: 'u-adviser' } as { id: string } | null,
	accessToken: 'tok',
	permissions,
	role: 'adviser'
});

function call(
	part: string,
	{
		upstream = new Response('bytes', { headers: { 'content-type': 'image/jpeg' } }),
		who = locals()
	}: { upstream?: Response; who?: ReturnType<typeof locals> } = {}
) {
	const fetch = vi.fn(async () => upstream);
	const result = GET({
		params: { uid: 'ct_1', check: 'chk_1', part },
		locals: who,
		fetch
	} as never);
	return { fetch, result };
}

describe('GET /clients/[uid]/compliance/identity/[check]/[part]', () => {
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
		const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toBe('http://127.0.0.1:8000/crm/contacts/ct_1/identity/checks/chk_1/images/front');
		expect(new Headers(init.headers).get('authorization')).toBe('Bearer tok');
	});

	it('passes an image content type through, inline and never cached', async () => {
		const { result } = call('selfie', {
			upstream: new Response('bytes', { headers: { 'content-type': 'image/png' } })
		});
		const res = (await result) as Response;
		expect(res.headers.get('content-type')).toBe('image/png');
		expect(res.headers.get('content-disposition')).toBe('inline');
		expect(res.headers.get('cache-control')).toBe('private, no-store');
	});

	it('needs screening:read, without calling the API', async () => {
		const denied = call('front', { who: locals(['contacts:write']) });
		await expect(denied.result).rejects.toMatchObject({ status: 403 });
		expect(denied.fetch).not.toHaveBeenCalled();
	});

	it('needs a signed-in user, without calling the API', async () => {
		const anonymous = call('front', { who: { ...locals(), user: null } });
		await expect(anonymous.result).rejects.toMatchObject({ status: 401 });
		expect(anonymous.fetch).not.toHaveBeenCalled();
	});

	it('404s on a part the relay does not know, without calling the API', async () => {
		const { fetch, result } = call('front-and-back');
		await expect(result).rejects.toMatchObject({ status: 404 });
		expect(fetch).not.toHaveBeenCalled();
	});

	it('passes a 404 from the API through as itself', async () => {
		const { result } = call('back', {
			upstream: new Response(JSON.stringify({ detail: 'No such image.' }), { status: 404 })
		});
		await expect(result).rejects.toMatchObject({
			status: 404,
			body: { message: 'No such image.' }
		});
	});

	it('maps an unreachable identity provider to 502', async () => {
		for (const status of [500, 502, 503]) {
			const { result } = call('front', { upstream: new Response(null, { status }) });
			await expect(result).rejects.toMatchObject({ status: 502 });
		}
	});
});
