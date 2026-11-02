/**
 * `GET /aml/checks/[uid]/report` — one check's report (identity or AML), relayed as a PDF
 * download so the browser never talks to the API directly.
 */
import { describe, expect, it, vi } from 'vitest';
import { GET } from './+server';

const locals = (permissions: string[] = ['aml:read']) => ({
	user: { id: 'u-ops' } as { id: string } | null,
	accessToken: 'tok',
	permissions,
	role: null
});

function call({
	upstream = new Response('%PDF', {
		headers: {
			'content-type': 'application/pdf',
			'content-disposition': 'attachment; filename="check-report.pdf"'
		}
	}),
	who = locals()
}: { upstream?: Response; who?: ReturnType<typeof locals> } = {}) {
	const fetch = vi.fn(async () => upstream);
	const result = GET({ params: { uid: 'chk_1' }, locals: who, fetch } as never);
	return { fetch, result };
}

describe('GET /aml/checks/[uid]/report', () => {
	it('relays the PDF with its type and file name, never cached', async () => {
		const { fetch, result } = call();
		const res = (await result) as Response;
		expect(await res.text()).toBe('%PDF');
		expect(res.headers.get('content-type')).toBe('application/pdf');
		expect(res.headers.get('content-disposition')).toBe('attachment; filename="check-report.pdf"');
		expect(res.headers.get('cache-control')).toBe('private, no-store');
		const [url] = fetch.mock.calls[0] as unknown as [string];
		expect(url).toBe('http://127.0.0.1:8000/aml/checks/chk_1/report');
	});

	it('needs aml:read, without calling the API', async () => {
		const { fetch, result } = call({ who: locals(['tenants:read']) });
		await expect(result).rejects.toMatchObject({ status: 403 });
		expect(fetch).not.toHaveBeenCalled();
	});

	it('passes a 404 from the API through with its reason', async () => {
		const { result } = call({
			upstream: new Response(JSON.stringify({ detail: 'There is no report of this check.' }), {
				status: 404
			})
		});
		await expect(result).rejects.toMatchObject({
			status: 404,
			body: { message: 'There is no report of this check.' }
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

	it('maps any other API failure to 502', async () => {
		const { result } = call({ upstream: new Response(null, { status: 500 }) });
		await expect(result).rejects.toMatchObject({ status: 502 });
	});
});
