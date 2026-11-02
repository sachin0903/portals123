/**
 * The workbench's loader and its acts. The loader only reads, reads a profile on the first render
 * only, and an old client-list link moves to where the list is now. A hits link opens the
 * workbench on its hits.
 */
import { describe, expect, it, vi } from 'vitest';
import { actions, load } from './+page.server';
import { load as hitsLoad } from './hits/+page.server';

const locals = (permissions: string[] = ['aml:read', 'aml:write', 'aml:adjudicate']) =>
	({
		user: { id: 'u-ops' },
		accessToken: 'tok',
		permissions,
		role: null
	}) as never;

type Call = { url: string; method: string; body: unknown };

/** A fetch that answers by URL path suffix, recording every call. */
function api(answers: Record<string, unknown>) {
	const calls: Call[] = [];
	const fetch = vi.fn(async (url: string | URL, init?: RequestInit) => {
		const href = String(url);
		calls.push({ url: href, method: init?.method ?? 'GET', body: init?.body });
		const match = Object.keys(answers)
			.sort((a, b) => b.length - a.length)
			.find((suffix) => href.split('?')[0].endsWith(suffix));
		if (!match) return new Response(JSON.stringify({ detail: 'Not found' }), { status: 404 });
		const answer = answers[match];
		return answer instanceof Response ? answer : new Response(JSON.stringify(answer));
	}) as unknown as typeof globalThis.fetch;
	return { calls, fetch };
}

const item = (key: string) => ({
	key,
	name: key,
	type: 'individual',
	firm: { uid: 't1', name: 'Ashworth', icon_url: null },
	origin: 'portal',
	waiting_since: '2026-10-01T09:00:00Z',
	age: { working_days: 3, band: 'ageing' },
	reasons: [],
	steps: []
});

const summary = {
	total: 2,
	by_reason: { hits: 1, identity: 1, screens: 0, monitoring: 0, held: 0 },
	overdue: 0
};

const QUEUE = {
	'/aml/work': { items: [item('ct_1'), item('ct_2')], total: 2, truncated: false },
	'/aml/work/summary': summary,
	'/admin/tenants': [{ uid: 't1', name: 'Ashworth' }],
	'/aml/clients/ct_1': { key: 'ct_1', client: { name: 'ct_1' } },
	'/aml/clients/ct_2': { key: 'ct_2', client: { name: 'ct_2' } }
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const run = (fn: unknown, event: Record<string, unknown>) => (fn as any)(event);

const at = (search = '') => new URL(`http://admin.test/aml${search}`);

/** The loader on a first render (a full page load), unless the event says otherwise. */
const loadWith = (event: Record<string, unknown>) =>
	run(load, { isDataRequest: false, untrack: <T>(fn: () => T) => fn(), ...event });

/**
 * A URL whose query reads are recorded as SvelteKit's are: a read outside `untrack` makes the
 * loader rerun when that parameter changes.
 */
function trackedUrl(search: string) {
	const url = at(search);
	const tracked = new Set<string>();
	let tracking = true;
	const params = url.searchParams;
	const read =
		(name: 'get' | 'has' | 'getAll') =>
		(param: string): unknown => {
			if (tracking) tracked.add(param);
			return params[name](param);
		};
	Object.defineProperty(url, 'searchParams', {
		value: { get: read('get'), has: read('has'), getAll: read('getAll') }
	});
	const untrack = <T>(fn: () => T): T => {
		tracking = false;
		try {
			return fn();
		} finally {
			tracking = true;
		}
	};
	return { url, tracked, untrack };
}

describe('the workbench loader', () => {
	it('opens the queue on its first profile, and only reads', async () => {
		const { fetch, calls } = api(QUEUE);
		const data = await loadWith({ url: at(), locals: locals(), fetch });
		expect(data.items.map((i: { key: string }) => i.key)).toEqual(['ct_1', 'ct_2']);
		expect(data.client.key).toBe('ct_1');
		expect(data.summary).toEqual(summary);
		expect(data.canWrite).toBe(true);
		expect(data.canAdjudicate).toBe(true);
		expect(calls.every((c) => c.method === 'GET')).toBe(true);
	});

	it('asks for what the "Show" choice names, and counts every reason', async () => {
		const { fetch, calls } = api(QUEUE);
		await loadWith({ url: at('?show=hits&tenant=t1&at=ct_2'), locals: locals(), fetch });
		const work = new URL(calls.find((c) => c.url.split('?')[0].endsWith('/aml/work'))!.url);
		expect(Object.fromEntries(work.searchParams)).toEqual({ reason: 'hits', tenant: 't1' });
		const counts = new URL(calls.find((c) => c.url.includes('/aml/work/summary'))!.url);
		expect(Object.fromEntries(counts.searchParams)).toEqual({ tenant: 't1' });
		expect(calls.some((c) => c.url.endsWith('/aml/clients/ct_2'))).toBe(true);

		// A choice the workbench no longer offers shows every profile.
		calls.length = 0;
		const data = await loadWith({ url: at('?show=others'), locals: locals(), fetch });
		expect(data.query.show).toBe('all');
		const all = calls.find((c) => c.url.split('?')[0].endsWith('/aml/work'))!.url;
		expect(all.endsWith('/aml/work')).toBe(true);
	});

	it('falls back to the first profile when the one in the URL is gone', async () => {
		const { fetch } = api(QUEUE);
		const data = await loadWith({ url: at('?at=ct_gone'), locals: locals(), fetch });
		expect(data.client.key).toBe('ct_1');
		expect(data.clientError).toBeNull();
	});

	it('shows an empty queue with no profile', async () => {
		const { fetch, calls } = api({
			...QUEUE,
			'/aml/work': { items: [], total: 0, truncated: false }
		});
		const data = await loadWith({ url: at(), locals: locals(), fetch });
		expect(data.client).toBeNull();
		expect(data.clientError).toBeNull();
		expect(calls.some((c) => c.url.includes('/aml/clients/'))).toBe(false);
	});

	it('reruns for a new "Show" or firm, never for a move between profiles', async () => {
		const { fetch } = api(QUEUE);
		const { url, tracked, untrack } = trackedUrl('?show=hits&tenant=t1&at=ct_2');
		const data = await loadWith({ url, untrack, locals: locals(), fetch });
		expect(data.query).toEqual({ show: 'hits', tenant: 't1', at: 'ct_2' });
		expect(data.client.key).toBe('ct_2');
		expect(tracked.has('show')).toBe(true);
		expect(tracked.has('tenant')).toBe(true);
		expect(tracked.has('at')).toBe(false);
	});

	it('reads the queue again on a rerun, and no profile: the page reads the one on screen', async () => {
		const { fetch, calls } = api(QUEUE);
		const data = await loadWith({
			url: at('?show=hits&at=ct_2'),
			isDataRequest: true,
			locals: locals(),
			fetch
		});
		expect(data.items.map((i: { key: string }) => i.key)).toEqual(['ct_1', 'ct_2']);
		expect(data.summary).toEqual(summary);
		expect(data.client).toBeNull();
		expect(data.clientError).toBeNull();
		expect(calls.some((c) => c.url.includes('/aml/clients/'))).toBe(false);
	});

	it('shows a reader every profile, read only', async () => {
		const { fetch } = api(QUEUE);
		const data = await loadWith({ url: at(), locals: locals(['aml:read']), fetch });
		expect(data.client.key).toBe('ct_1');
		expect(data.canWrite).toBe(false);
		expect(data.canAdjudicate).toBe(false);
	});

	it('moves an old client-list link to the client list, query and all', async () => {
		const { fetch, calls } = api(QUEUE);
		await expect(
			loadWith({ url: at('?q=ada&sort=risk&dir=desc&page=2'), locals: locals(), fetch })
		).rejects.toMatchObject({
			status: 308,
			location: '/aml/clients?q=ada&sort=risk&dir=desc&page=2'
		});
		expect(calls).toHaveLength(0);
	});

	it('refuses a visitor without aml:read', async () => {
		const { fetch } = api(QUEUE);
		await expect(
			loadWith({ url: at(), locals: locals(['tenants:read']), fetch })
		).rejects.toMatchObject({ status: 403 });
	});
});

describe('a hits link', () => {
	const open = async (search = '') =>
		run(hitsLoad, { url: new URL(`http://admin.test/aml/hits${search}`) });

	it('opens the workbench on the hits to review', async () => {
		await expect(open()).rejects.toMatchObject({ status: 308, location: '/aml?show=hits' });
	});

	it('keeps its firm, and leaves the rest behind', async () => {
		await expect(open('?tenant=t1&q=smith&page=2')).rejects.toMatchObject({
			status: 308,
			location: '/aml?show=hits&tenant=t1'
		});
	});
});

describe('the workbench acts', () => {
	it('offers every act the check sections post', () => {
		expect(Object.keys(actions).sort()).toEqual([
			'allowJourney',
			'decideCheck',
			'decideMatch',
			'monitoring',
			'requestPhotos',
			'rerun',
			'screen'
		]);
	});

	it('screens a client and stays on the workbench', async () => {
		const { fetch, calls } = api({
			'/aml/screen': { check_uid: 'chk_new' },
			'/aml/checks/chk_new/client': { key: 'cl_new', check_uid: 'chk_new' }
		});
		const body = new FormData();
		body.append('contact_uid', 'c1');
		const result = await run(actions.screen, {
			request: new Request('http://admin.test/aml', { method: 'POST', body }),
			locals: locals(),
			fetch
		});
		expect(result).toEqual({ action: 'screen' });
		expect(calls.map((c) => c.url.replace(/^.*\/aml/, '/aml'))).toEqual(['/aml/screen']);
	});
});
