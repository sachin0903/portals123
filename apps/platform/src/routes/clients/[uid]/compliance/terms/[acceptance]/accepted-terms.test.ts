/**
 * `GET /clients/[uid]/compliance/terms/[acceptance]` and its `/file`: the terms a client accepted,
 * through this app so the browser never talks to the API directly.
 */
import { describe, expect, it, vi } from 'vitest';
import { GET as getTerms } from './+server';
import { GET as getFile } from './file/+server';

const locals = (permissions: string[] = ['contacts:read']) => ({
	user: { id: 'u-adviser' } as { id: string } | null,
	accessToken: 'tok',
	permissions,
	role: 'adviser'
});

function call(
	handler: (event: never) => unknown,
	upstream: Response,
	who = locals()
): { fetch: ReturnType<typeof vi.fn>; result: Promise<unknown> } {
	const fetch = vi.fn(async () => upstream);
	const result = (async () =>
		handler({ params: { uid: 'ct_1', acceptance: 'acc_1' }, locals: who, fetch } as never))();
	return { fetch, result };
}

const TERMS = { uid: 'acc_1', title: 'General declaration', version_number: 1, has_file: false };

describe('GET …/compliance/terms/[acceptance]', () => {
	it('relays the accepted version, never cached', async () => {
		const { fetch, result } = call(getTerms, Response.json(TERMS));
		const res = (await result) as Response;
		expect(await res.json()).toEqual(TERMS);
		expect(res.headers.get('cache-control')).toBe('private, no-store');
		const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toBe('http://127.0.0.1:8000/contacts/ct_1/term-acceptances/acc_1');
		expect(new Headers(init.headers).get('authorization')).toBe('Bearer tok');
	});

	it('refuses a visitor without contacts:read, before any fetch', async () => {
		const { fetch, result } = call(getTerms, Response.json(TERMS), locals([]));
		await expect(result).rejects.toMatchObject({ status: 403 });
		expect(fetch).not.toHaveBeenCalled();
	});

	it('passes a 404 through', async () => {
		const { result } = call(getTerms, Response.json({ detail: 'Not found.' }, { status: 404 }));
		await expect(result).rejects.toMatchObject({ status: 404 });
	});
});

describe('GET …/compliance/terms/[acceptance]/file', () => {
	it('relays the version’s file inline, never cached', async () => {
		const upstream = new Response('%PDF', {
			headers: {
				'content-type': 'application/pdf',
				'content-disposition': 'inline; filename="gd.pdf"'
			}
		});
		const { fetch, result } = call(getFile, upstream);
		const res = (await result) as Response;
		expect(await res.text()).toBe('%PDF');
		expect(res.headers.get('content-type')).toBe('application/pdf');
		expect(res.headers.get('content-disposition')).toBe('inline; filename="gd.pdf"');
		expect(res.headers.get('cache-control')).toBe('private, no-store');
		const [url] = fetch.mock.calls[0] as unknown as [string];
		expect(url).toBe('http://127.0.0.1:8000/contacts/ct_1/term-acceptances/acc_1/file');
	});

	it('answers 502 when the API fails', async () => {
		const { result } = call(getFile, new Response('x', { status: 500 }));
		await expect(result).rejects.toMatchObject({ status: 502 });
	});
});
