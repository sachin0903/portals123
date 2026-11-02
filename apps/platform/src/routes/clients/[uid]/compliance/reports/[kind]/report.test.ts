/**
 * `GET /clients/[uid]/compliance/reports/[kind]` — one compliance report on a client (the client
 * report, an identity check's, or the AML screen's), relayed as a PDF download so the browser never
 * talks to the API directly.
 */
import { describe, expect, it, vi } from 'vitest';
import { GET } from './+server';

const locals = (permissions: string[] = ['screening:read']) => ({
	user: { id: 'u-adviser' } as { id: string } | null,
	accessToken: 'tok',
	permissions,
	role: 'adviser'
});

const PDF = () =>
	new Response('%PDF', {
		headers: {
			'content-type': 'application/pdf',
			'content-disposition': 'attachment; filename="identity-check.pdf"'
		}
	});

function call(
	kind: string,
	{
		upstream = PDF() as Response | (() => Promise<Response>),
		who = locals(),
		query = ''
	}: {
		upstream?: Response | (() => Promise<Response>);
		who?: ReturnType<typeof locals>;
		query?: string;
	} = {}
) {
	const fetch = vi.fn(async () => (typeof upstream === 'function' ? upstream() : upstream));
	const result = GET({
		params: { uid: 'ct_1', kind },
		locals: who,
		fetch,
		url: new URL(`http://localhost/clients/ct_1/compliance/reports/${kind}${query}`)
	} as never);
	return { fetch, result };
}

const calledUrl = (fetch: ReturnType<typeof vi.fn>) =>
	(fetch.mock.calls[0] as unknown as [string, RequestInit])[0];

describe('GET /clients/[uid]/compliance/reports/[kind]', () => {
	it('relays the PDF with its type and file name, never cached', async () => {
		const { fetch, result } = call('client');
		const res = (await result) as Response;
		expect(await res.text()).toBe('%PDF');
		expect(res.headers.get('content-type')).toBe('application/pdf');
		expect(res.headers.get('content-disposition')).toBe(
			'attachment; filename="identity-check.pdf"'
		);
		expect(res.headers.get('cache-control')).toBe('private, no-store');
		const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toBe('http://127.0.0.1:8000/crm/contacts/ct_1/compliance-reports/client');
		expect(new Headers(init.headers).get('authorization')).toBe('Bearer tok');
	});

	it('asks for the identity check named by ?check=', async () => {
		const { fetch, result } = call('identity', { query: '?check=chk_1' });
		await result;
		expect(calledUrl(fetch)).toBe(
			'http://127.0.0.1:8000/crm/contacts/ct_1/compliance-reports/identity?check=chk_1'
		);

		const latest = call('identity');
		await latest.result;
		expect(calledUrl(latest.fetch)).toBe(
			'http://127.0.0.1:8000/crm/contacts/ct_1/compliance-reports/identity'
		);
	});

	it('sends ?check= for the identity report only', async () => {
		const { fetch, result } = call('aml', { query: '?check=chk_1' });
		await result;
		expect(calledUrl(fetch)).toBe('http://127.0.0.1:8000/crm/contacts/ct_1/compliance-reports/aml');
	});

	it('404s on a report the relay does not know, without calling the API', async () => {
		for (const kind of ['application', 'risk', '..']) {
			const { fetch, result } = call(kind);
			await expect(result).rejects.toMatchObject({ status: 404 });
			expect(fetch).not.toHaveBeenCalled();
		}
	});

	it('needs screening:read and a signed-in user, without calling the API', async () => {
		const denied = call('client', { who: locals(['contacts:write']) });
		await expect(denied.result).rejects.toMatchObject({ status: 403 });
		expect(denied.fetch).not.toHaveBeenCalled();

		const anonymous = call('client', { who: { ...locals(), user: null } });
		await expect(anonymous.result).rejects.toMatchObject({ status: 401 });
		expect(anonymous.fetch).not.toHaveBeenCalled();
	});

	it('passes a 409 from the API through with its reason', async () => {
		const { result } = call('aml', {
			upstream: new Response(JSON.stringify({ detail: 'Available once the screen is clear.' }), {
				status: 409
			})
		});
		await expect(result).rejects.toMatchObject({
			status: 409,
			body: { message: 'Available once the screen is clear.' }
		});
	});

	it('passes a 503 from the API through with its reason', async () => {
		const { result } = call('client', {
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
			const { result } = call('client', { upstream: new Response(null, { status }) });
			await expect(result).rejects.toMatchObject({ status: 502 });
		}
		const down = call('client', {
			upstream: async () => {
				throw new Error('network down');
			}
		});
		await expect(down.result).rejects.toMatchObject({ status: 503 });
	});
});
