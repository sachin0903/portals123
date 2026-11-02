/**
 * The content library loader. `content:read` is a synchronous short-circuit (a resolved empty
 * feed, never a round trip that looks like "no content"), and a caller who may read gets the
 * query echo immediately with the items still in flight. A failed read sets `loadError` so the
 * page shows a banner rather than an empty library.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

type LoadEvent = Parameters<typeof load>[0];

const ITEMS = [{ uid: 'cnt_1', title: 'ISA guide', status: 'published', kind: 'guide' }];

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});

function event(
	over: { search?: string; permissions?: string[]; fetch?: typeof globalThis.fetch } = {}
) {
	const fetch = over.fetch ?? vi.fn(async () => json(ITEMS));
	return {
		url: new URL(`http://portal.test/content${over.search ?? ''}`),
		locals: {
			user: { id: 'user_1' },
			accessToken: 'token',
			permissions: over.permissions ?? ['content:read'],
			role: 'adviser'
		},
		fetch
	} as unknown as LoadEvent & { fetch: ReturnType<typeof vi.fn> };
}

type Loaded = Exclude<Awaited<ReturnType<typeof load>>, void>;

describe('the /content loader streams the items', () => {
	it('returns before the API answers, with the query echo already in hand', async () => {
		// The toolbar paints from these. A loader that awaited the items here would hold the
		// whole page — search box included — until the library responded.
		const e = event({
			search: '?q=isa&kind=article&kind=guide&status=draft',
			permissions: ['content:read', 'content:write'],
			fetch: vi.fn(() => new Promise<Response>(() => {}))
		});
		const data = (await load(e)) as Loaded;
		expect(data.feed).toBeInstanceOf(Promise);
		expect(data.q).toBe('isa');
		expect(data.kind).toEqual(['article', 'guide']);
		expect(data.status).toEqual(['draft']);
		expect(data.view).toBe('current');
		expect(data.canManage).toBe(true);
	});
});

describe('the /content loader gate', () => {
	it('bounces an unauthenticated caller to /login', async () => {
		const e = event();
		(e.locals as { user: unknown }).user = null;
		await expect(load(e)).rejects.toMatchObject({ status: 303, location: '/login' });
	});

	it('short-circuits without content:read — a resolved empty feed, and no API call', async () => {
		const e = event({ permissions: ['contacts:read'], search: '?q=isa&kind=article' });
		const data = (await load(e)) as Loaded;
		expect(data.feed).not.toBeInstanceOf(Promise);
		expect(data.feed).toEqual({ items: [], loadError: false });
		expect(data.canManage).toBe(false);
		expect(data.q).toBe('isa');
		expect(data.kind).toEqual(['article']);
		expect(e.fetch).not.toHaveBeenCalled();
	});

	it('lets content:write through (it satisfies content:read) and may manage', async () => {
		const data = (await load(event({ permissions: ['content:write'] }))) as Loaded;
		expect(data.canManage).toBe(true);
		await expect(data.feed).resolves.toMatchObject({ items: ITEMS, loadError: false });
	});
});

describe('the /content loader builds the items query from the echo', () => {
	it('forwards search, every kind and every status on the current view', async () => {
		const e = event({
			search: '?q=isa&kind=article&kind=guide&status=draft&status=published'
		});
		await load(e);
		const called = new URL(String(e.fetch.mock.calls[0][0]));
		expect(called.pathname).toBe('/content/items');
		expect(called.searchParams.get('q')).toBe('isa');
		expect(called.searchParams.getAll('kind')).toEqual(['article', 'guide']);
		expect(called.searchParams.getAll('status')).toEqual(['draft', 'published']);
	});

	it('does not send status on the archived view — that tab is the archive, not a status', async () => {
		const e = event({ search: '?status=draft&view=archived&q=isa' });
		const data = (await load(e)) as Loaded;
		expect(data.view).toBe('archived');
		expect(data.status).toEqual([]);
		expect(data.q).toBe('isa');
		const called = new URL(String(e.fetch.mock.calls[0][0]));
		expect(called.searchParams.get('q')).toBe('isa');
		expect(called.searchParams.has('status')).toBe(false);
	});
});

describe('the /content loader failure modes', () => {
	it('sets loadError when the items read fails, rather than an empty library', async () => {
		const e = event({ fetch: vi.fn(async () => json({ detail: 'nope' }, 500)) });
		const data = (await load(e)) as Loaded;
		expect(data.feed).toBeInstanceOf(Promise);
		await expect(data.feed).resolves.toEqual({ items: [], loadError: true });
	});

	it('sets loadError when the items read throws, and does not reject', async () => {
		const e = event({
			fetch: vi.fn(async () => {
				throw new Error('ECONNREFUSED');
			})
		});
		const data = (await load(e)) as Loaded;
		await expect(data.feed).resolves.toMatchObject({ items: [], loadError: true });
	});
});
