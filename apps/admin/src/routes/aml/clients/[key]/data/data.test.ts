/**
 * `GET /aml/clients/[key]/data` — one client's page body for the workbench, relayed so the browser
 * never talks to the API. `?images=false` reads it without listing the photos.
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
	search = '',
	{
		upstream = new Response(JSON.stringify({ key: 'ct 1' }), {
			headers: { 'content-type': 'application/json' }
		}),
		who = locals()
	}: { upstream?: Response | Error; who?: ReturnType<typeof locals> } = {}
) {
	const fetch = vi.fn(async () => {
		if (upstream instanceof Error) throw upstream;
		return upstream;
	});
	const url = new URL(`http://admin.test/aml/clients/ct%201/data${search}`);
	const result = GET({
		params: { key: 'ct 1' },
		url,
		locals: who,
		fetch
	} as never) as Promise<Response>;
	return { fetch, result };
}

const asked = (fetch: ReturnType<typeof vi.fn>) => (fetch.mock.calls[0] as unknown as [string])[0];

describe('GET /aml/clients/[key]/data', () => {
	it('relays the page body with the photos, never cached', async () => {
		const { fetch, result } = call();
		const res = await result;
		expect(res.status).toBe(200);
		expect(await res.json()).toEqual({ key: 'ct 1' });
		expect(res.headers.get('content-type')).toBe('application/json');
		expect(res.headers.get('cache-control')).toBe('private, no-store');
		expect(asked(fetch)).toBe('http://127.0.0.1:8000/aml/clients/ct%201');
		const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(init.headers).toEqual({ Authorization: 'Bearer tok' });
	});

	it('forwards images=false, and nothing else', async () => {
		const { fetch, result } = call('?images=false&at=ct_9');
		await result;
		expect(asked(fetch)).toBe('http://127.0.0.1:8000/aml/clients/ct%201?images=false');

		const other = call('?images=true');
		await other.result;
		expect(asked(other.fetch)).toBe('http://127.0.0.1:8000/aml/clients/ct%201');
	});

	it('passes a refusal through with its status and reason', async () => {
		const { result } = call('', {
			upstream: new Response(JSON.stringify({ detail: 'Client not found.' }), { status: 404 })
		});
		const res = await result;
		expect(res.status).toBe(404);
		expect(await res.json()).toEqual({ detail: 'Client not found.' });
	});

	it('answers 503 when the API cannot be reached', async () => {
		const { result } = call('', { upstream: new Error('ECONNREFUSED') });
		const res = await result;
		expect(res.status).toBe(503);
		expect(await res.json()).toEqual({ detail: 'The service is unavailable.' });
	});

	it('needs a signed-in user with aml:read, before asking the API', async () => {
		const anonymous = call('', { who: { ...locals(), user: null } });
		expect((await anonymous.result).status).toBe(401);
		expect(anonymous.fetch).not.toHaveBeenCalled();

		const denied = call('', { who: locals(['tenants:read']) });
		const res = await denied.result;
		expect(res.status).toBe(403);
		expect(await res.json()).toEqual({ detail: 'Not authorised' });
		expect(denied.fetch).not.toHaveBeenCalled();
	});
});
