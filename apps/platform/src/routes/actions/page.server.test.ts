/**
 * The actions loader: one route, three tabs. Runs (the default) streams one server-paged page of
 * the runs workbench, with the picked run's detail beside it when `?run=` names one; Approvals
 * streams the held runs; Policies streams the editor — the gate is answered before the first
 * byte, the board behind it.
 */
import { describe, expect, it } from 'vitest';
import { load, type ActionsBoard, type RunsBoard } from './+page.server';

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});

const CATALOGUE = {
	categories: [{ key: 'money', label: 'Money' }],
	actions: [
		{ key: 'withdraw', label: 'Withdraw', category: 'money' },
		{ key: 'transfer', label: 'Transfer', category: 'money' }
	]
};
const ROLES = [
	{ slug: 'adviser', name: 'Adviser', portal: 'platform' },
	{ slug: 'client', name: 'Client', portal: 'client' }
];
const PAGE = {
	items: [
		{ uid: 'run_1', action_key: 'withdraw', name: 'Withdraw', status: 'running', children: [] }
	],
	total: 1,
	limit: 25,
	offset: 0,
	facets: { state: ['running'] },
	stats: {
		running: 1,
		waiting: 0,
		awaiting_approval: 0,
		needs_attention: 0,
		failed_7d: 0,
		completed_24h: 4
	}
};
const DETAIL = { uid: 'run_1', action_key: 'withdraw', name: 'Withdraw', status: 'running' };

/** Answers the surfaces and records the ORDER the calls were made in. */
function harness() {
	const urls: string[] = [];
	const fetch = (async (input: string | URL) => {
		const url = String(input);
		urls.push(url);
		if (url.endsWith('/actions/catalogue')) return json(CATALOGUE);
		if (url.endsWith('/firm/roles')) return json(ROLES);
		if (url.includes('/actions/approvals')) {
			return json({
				approvals: [{ uid: 'run_9', action_key: 'transaction.transfer', can_decide: true }]
			});
		}
		if (url.includes('/actions/runs/run_1')) return json(DETAIL);
		if (url.includes('/actions/runs')) return json(PAGE); // the workbench page
		return json({}, 404);
	}) as unknown as typeof globalThis.fetch;
	return { fetch, urls };
}

const loadAt = async (
	fetch: typeof globalThis.fetch,
	{ permissions = ['actions:read'], search = '' }: { permissions?: string[]; search?: string } = {}
) =>
	load({
		locals: { user: { id: 'u_1' }, accessToken: 'tok', permissions, role: null },
		fetch,
		url: new URL(`http://portal.test/actions${search}`),
		depends: () => {}
	} as never);

const policies = async (
	fetch: typeof globalThis.fetch,
	opts: { permissions?: string[]; action?: string } = {}
) => {
	const action = opts.action ? `&action=${opts.action}` : '';
	return (await loadAt(fetch, {
		permissions: opts.permissions,
		search: `?tab=policies${action}`
	})) as { board: Promise<ActionsBoard>; canManage: boolean };
};

describe('the actions loader', () => {
	it('refuses without actions:read or actions:write, before touching the API', async () => {
		const { fetch, urls } = harness();
		await expect(loadAt(fetch, { permissions: ['tasks:read'] })).rejects.toMatchObject({
			status: 403
		});
		expect(urls).toEqual([]);
	});

	it('defaults to the Runs tab: one server-paged page with its counts', async () => {
		const { fetch, urls } = harness();
		const result = (await loadAt(fetch, { permissions: ['actions:write'] })) as {
			tab: string;
			runs: Promise<RunsBoard>;
			selected: Promise<unknown> | null;
		};
		expect(result.tab).toBe('runs');
		expect(result.runs).toBeInstanceOf(Promise);
		expect(result.selected).toBeNull();
		const board = await result.runs;
		expect(board.loadError).toBe(false);
		expect(board.page.items.map((r) => r.uid)).toEqual(['run_1']);
		expect(board.page.stats.completed_24h).toBe(4);
		// The API pages: the read names the page size and the default order, and nothing else.
		const read = urls.find((u) => u.includes('/actions/runs'))!;
		expect(read).toContain('limit=25');
		expect(read).toContain('sort=requested');
		expect(read).toContain('dir=desc');
		// It must not have asked the catalogue or the approvals.
		expect(urls.some((u) => u.includes('/actions/catalogue'))).toBe(false);
		expect(urls.some((u) => u.includes('/actions/approvals'))).toBe(false);
	});

	it('passes the search, the filters and the page through to the runs read', async () => {
		const { fetch, urls } = harness();
		const result = (await loadAt(fetch, {
			search: '?q=okonkwo&state=needs_attention&provider=sei&offset=25'
		})) as { query: { q: string; filters: Record<string, string | string[]>; offset: number } };
		expect(result.query.q).toBe('okonkwo');
		expect(result.query.filters).toEqual({ state: 'needs_attention', provider: 'sei' });
		expect(result.query.offset).toBe(25);
		const read = urls.find((u) => u.includes('/actions/runs'))!;
		expect(read).toContain('q=okonkwo');
		expect(read).toContain('state=needs_attention');
		expect(read).toContain('provider=sei');
		expect(read).toContain('offset=25');
	});

	it('does not forward a param that is not a filter (`run`, `tab`)', async () => {
		const { fetch, urls } = harness();
		await loadAt(fetch, { search: '?run=run_1&state=running' });
		const read = urls.find((u) => u.includes('/actions/runs?'))!;
		expect(read).not.toContain('run=');
		expect(read).not.toContain('tab=');
	});

	it('streams the picked run’s detail beside the page when `?run=` names one', async () => {
		const { fetch, urls } = harness();
		const result = (await loadAt(fetch, { search: '?run=run_1' })) as {
			selectedUid: string | null;
			selected: Promise<{ uid: string } | null>;
		};
		expect(result.selectedUid).toBe('run_1');
		const detail = await result.selected;
		expect(detail).toMatchObject({ uid: 'run_1' });
		expect(urls.some((u) => u.endsWith('/actions/runs/run_1'))).toBe(true);
	});

	it('shows no panel for a run that is gone, rather than failing the page', async () => {
		const fetch = (async (input: string | URL) =>
			String(input).includes('/actions/runs/')
				? json({}, 404)
				: json(PAGE)) as unknown as typeof globalThis.fetch;
		const result = (await loadAt(fetch, { search: '?run=run_x' })) as {
			selected: Promise<unknown>;
			runs: Promise<RunsBoard>;
		};
		expect(await result.selected).toBeNull();
		expect((await result.runs).loadError).toBe(false);
	});

	it('streams the Approvals tab as the runs workbench locked to the gate, oldest first', async () => {
		const { fetch, urls } = harness();
		const result = (await loadAt(fetch, { search: '?tab=approvals&state=failed&held=money' })) as {
			tab: string;
			runs: Promise<RunsBoard>;
			query: { filters: Record<string, string | string[]>; sort: string | null; dir: string };
		};
		expect(result.tab).toBe('approvals');
		const board = await result.runs;
		expect(board.loadError).toBe(false);
		expect(board.page.items.map((r) => r.uid)).toEqual(['run_1']);
		// the state is pinned whatever the URL says; the seat's own filter goes through
		expect(result.query.filters).toEqual({ state: 'awaiting_approval', held: 'money' });
		const read = urls.find((u) => u.includes('/actions/runs?'))!;
		expect(read).toContain('state=awaiting_approval');
		expect(read).not.toContain('state=failed');
		expect(read).toContain('held=money');
		expect(read).toContain('sort=requested');
		expect(read).toContain('dir=asc');
		// the card list's own endpoint is no longer read
		expect(urls.some((u) => u.includes('/actions/approvals'))).toBe(false);
	});

	it('streams the Policies board so the first byte does not wait on the catalogue', async () => {
		const { fetch, urls } = harness();
		const result = await policies(fetch, { permissions: ['actions:write'] });
		expect(result.canManage).toBe(true);
		expect(result.board).toBeInstanceOf(Promise);
		const board = await result.board;
		expect(board.loadError).toBe(false);
		expect(board.actions.map((a) => a.key)).toEqual(['withdraw', 'transfer']);
		// Only the platform's roles are offered, and no runs are read for the editor.
		expect(board.roles.map((r) => r.slug)).toEqual(['adviser']);
		expect(urls.some((u) => u.includes('/actions/runs'))).toBe(false);
	});

	it('selects the requested action, falling back to the first when it is not in the catalogue', async () => {
		const { fetch } = harness();
		expect((await (await policies(fetch, { action: 'transfer' })).board).selected).toBe('transfer');
		expect((await (await policies(fetch, { action: 'nonsense' })).board).selected).toBe('withdraw');
		expect((await (await policies(fetch)).board).selected).toBe('withdraw');
	});

	it('reports a catalogue failure instead of an empty editor', async () => {
		const fetch = (async (input: string | URL) =>
			String(input).endsWith('/actions/catalogue')
				? json({}, 500)
				: json(ROLES)) as unknown as typeof globalThis.fetch;
		const board = await (await policies(fetch)).board;
		expect(board).toMatchObject({ loadError: true, actions: [], selected: null });
	});
});
