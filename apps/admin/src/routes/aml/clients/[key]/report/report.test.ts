/**
 * `GET /aml/clients/[key]/report` — the client report, relayed as a PDF download so the browser
 * never talks to the API directly.
 */
import { describe, expect, it, vi } from 'vitest';
import { GET } from './+server';

const locals = (permissions: string[] = ['aml:read']) => ({
	user: { id: 'u-ops' } as { id: string } | null,
	accessToken: 'tok',
	permissions,
	role: null
});

const PDF = () =>
	new Response('%PDF', {
		headers: {
			'content-type': 'application/pdf',
			'content-disposition': 'attachment; filename="client-report.pdf"'
		}
	});

function call({
	upstream = PDF() as Response | (() => Promise<Response>),
	who = locals()
}: { upstream?: Response | (() => Promise<Response>); who?: ReturnType<typeof locals> } = {}) {
	const fetch = vi.fn(async () => (typeof upstream === 'function' ? upstream() : upstream));
	const result = GET({ params: { key: 'ct_1' }, locals: who, fetch } as never);
	return { fetch, result };
}

describe('GET /aml/clients/[key]/report', () => {
	it('relays the PDF with its type and file name, never cached', async () => {
		const { fetch, result } = call();
		const res = (await result) as Response;
		expect(await res.text()).toBe('%PDF');
		expect(res.headers.get('content-type')).toBe('application/pdf');
		expect(res.headers.get('content-disposition')).toBe('attachment; filename="client-report.pdf"');
		expect(res.headers.get('cache-control')).toBe('private, no-store');
		const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toBe('http://127.0.0.1:8000/aml/clients/ct_1/report');
		expect(new Headers(init.headers).get('authorization')).toBe('Bearer tok');
	});

	it('needs aml:read and a signed-in user, without calling the API', async () => {
		const denied = call({ who: locals([]) });
		await expect(denied.result).rejects.toMatchObject({ status: 403 });
		expect(denied.fetch).not.toHaveBeenCalled();

		const anonymous = call({ who: { ...locals(), user: null } });
		await expect(anonymous.result).rejects.toMatchObject({ status: 303, location: '/login' });
		expect(anonymous.fetch).not.toHaveBeenCalled();
	});

	it('passes a 404 from the API through with its reason', async () => {
		const { result } = call({
			upstream: new Response(JSON.stringify({ detail: 'This client has not been checked yet.' }), {
				status: 404
			})
		});
		await expect(result).rejects.toMatchObject({
			status: 404,
			body: { message: 'This client has not been checked yet.' }
		});
	});

	it('passes a 503 from the API through with its reason', async () => {
		const { result } = call({
			upstream: new Response(
				JSON.stringify({ detail: 'The report could not be made. Try again.' }),
				{ status: 503 }
			)
		});
		await expect(result).rejects.toMatchObject({
			status: 503,
			body: { message: 'The report could not be made. Try again.' }
		});
	});

	it('maps any other API failure to 502, and no answer to 503', async () => {
		for (const status of [500, 502]) {
			const { result } = call({ upstream: new Response(null, { status }) });
			await expect(result).rejects.toMatchObject({ status: 502 });
		}
		const down = call({
			upstream: async () => {
				throw new Error('network down');
			}
		});
		await expect(down.result).rejects.toMatchObject({ status: 503 });
	});
});
