import { afterEach, describe, expect, it, vi } from 'vitest';
import { postShare } from './share-action';

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('postShare', () => {
	it('names the request as a form action so generate does not take it', async () => {
		const fetchMock = vi.fn(async () => new Response('{}'));
		vi.stubGlobal('fetch', fetchMock);

		await postShare('ill_1');

		expect(fetchMock).toHaveBeenCalledOnce();
		const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toBe('?/share');
		expect(init.method).toBe('POST');
		const headers = new Headers(init.headers);
		expect(headers.get('x-sveltekit-action')).toBe('true');
		expect(headers.get('accept')).toBe('application/json');
		expect(init.body).toBeInstanceOf(FormData);
		expect((init.body as FormData).get('uid')).toBe('ill_1');
	});
});
