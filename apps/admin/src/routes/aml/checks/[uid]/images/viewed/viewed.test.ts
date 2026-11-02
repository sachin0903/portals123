/**
 * `POST /aml/checks/[uid]/images/viewed` — records that photos the workbench read ahead were shown,
 * relayed to the API, which writes the "identity images viewed" event.
 */
import { describe, expect, it, vi } from 'vitest';
import { POST } from './+server';

const locals = (permissions: string[] = ['aml:read']) => ({
	user: { id: 'u-ops' } as { id: string } | null,
	accessToken: 'tok',
	permissions,
	role: null
});

function call({
	body = JSON.stringify({ image_count: 2 }),
	upstream = new Response(null, { status: 204 }),
	who = locals()
}: {
	body?: string;
	upstream?: Response | Error;
	who?: ReturnType<typeof locals>;
} = {}) {
	const fetch = vi.fn(async () => {
		if (upstream instanceof Error) throw upstream;
		return upstream;
	});
	const request = new Request('http://admin.test/aml/checks/chk_1/images/viewed', {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body
	});
	const result = POST({
		params: { uid: 'chk_1' },
		request,
		locals: who,
		fetch
	} as never) as Promise<Response>;
	return { fetch, result };
}

describe('POST /aml/checks/[uid]/images/viewed', () => {
	it('forwards the count to the API as JSON, and its answer back', async () => {
		const { fetch, result } = call();
		const res = await result;
		expect(res.status).toBe(204);
		expect(res.headers.get('cache-control')).toBe('private, no-store');
		const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toBe('http://127.0.0.1:8000/aml/checks/chk_1/images/viewed');
		expect(init.method).toBe('POST');
		expect(init.headers).toEqual({
			Authorization: 'Bearer tok',
			'Content-Type': 'application/json'
		});
		expect(JSON.parse(init.body as string)).toEqual({ image_count: 2 });
	});

	it('passes a refusal through: an AML check has no photos', async () => {
		const { result } = call({
			upstream: new Response(JSON.stringify({ detail: 'Not an identity check.' }), { status: 404 })
		});
		const res = await result;
		expect(res.status).toBe(404);
		expect(await res.json()).toEqual({ detail: 'Not an identity check.' });
	});

	it('refuses a body it cannot read, without asking the API', async () => {
		const { fetch, result } = call({ body: 'not json' });
		const res = await result;
		expect(res.status).toBe(400);
		expect(fetch).not.toHaveBeenCalled();
	});

	it('answers 503 when the API cannot be reached', async () => {
		const res = await call({ upstream: new Error('ECONNREFUSED') }).result;
		expect(res.status).toBe(503);
	});

	it('needs a signed-in user with aml:read, before asking the API', async () => {
		const anonymous = call({ who: { ...locals(), user: null } });
		expect((await anonymous.result).status).toBe(401);
		expect(anonymous.fetch).not.toHaveBeenCalled();

		const denied = call({ who: locals([]) });
		expect((await denied.result).status).toBe(403);
		expect(denied.fetch).not.toHaveBeenCalled();
	});
});
