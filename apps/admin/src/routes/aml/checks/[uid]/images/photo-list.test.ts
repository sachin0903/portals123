/**
 * `GET /aml/checks/[uid]/images` — the photos one identity check holds, listed for the profile on
 * screen. The API records the view; each photo's bytes come from `[part]`.
 */
import { describe, expect, it, vi } from 'vitest';
import { GET } from './+server';

const locals = (permissions: string[] = ['aml:read']) => ({
	user: { id: 'u-ops' } as { id: string } | null,
	accessToken: 'tok',
	permissions,
	role: null
});

const LISTING = {
	images: [{ part: 'front', label: 'Front', content_type: 'image/jpeg', source: 'complycube' }],
	images_missing: null
};

function call({
	upstream = new Response(JSON.stringify(LISTING), {
		headers: { 'content-type': 'application/json' }
	}),
	who = locals(),
	search = ''
}: { upstream?: Response; who?: ReturnType<typeof locals>; search?: string } = {}) {
	const fetch = vi.fn(async () => upstream);
	const result = GET({
		params: { uid: 'chk_1' },
		url: new URL(`http://admin.test/aml/checks/chk_1/images${search}`),
		locals: who,
		fetch
	} as never) as Promise<Response>;
	return { fetch, result };
}

describe('GET /aml/checks/[uid]/images', () => {
	it('relays the listing, never cached', async () => {
		const { fetch, result } = call();
		const res = await result;
		expect(res.status).toBe(200);
		expect(await res.json()).toEqual(LISTING);
		expect(res.headers.get('cache-control')).toBe('private, no-store');
		const [url] = fetch.mock.calls[0] as unknown as [string];
		expect(url).toBe('http://127.0.0.1:8000/aml/checks/chk_1/images');
	});

	it('forwards record=false, and nothing else', async () => {
		const ahead = call({ search: '?record=false&at=ct_9' });
		await ahead.result;
		const [url] = ahead.fetch.mock.calls[0] as unknown as [string];
		expect(url).toBe('http://127.0.0.1:8000/aml/checks/chk_1/images?record=false');

		const other = call({ search: '?record=true' });
		await other.result;
		const [plain] = other.fetch.mock.calls[0] as unknown as [string];
		expect(plain).toBe('http://127.0.0.1:8000/aml/checks/chk_1/images');
	});

	it('passes a refusal through: an AML check has no photos', async () => {
		const { result } = call({
			upstream: new Response(JSON.stringify({ detail: 'Not an identity check.' }), { status: 404 })
		});
		const res = await result;
		expect(res.status).toBe(404);
		expect(await res.json()).toEqual({ detail: 'Not an identity check.' });
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
