/**
 * The datasets routes' server contracts — the parts a user notices when the API says no.
 *
 * A dataset carries firm-authored SQL, so the failure paths matter as much as the happy one: a
 * failed list must raise a retry banner rather than render as "no datasets"; a refused "Run query"
 * must show the API's own reason (invalid query, unseen client, clipped result) instead of an empty
 * grid; a refused delete must repeat the API's "N template(s) still use this dataset"; and the
 * details Sheet must not rewrite a field it didn't offer.
 *
 * The pages also decide which SCOPE options to offer: a firm-wide (global) SQL dataset is resolved
 * across the whole firm when a document is generated, so the API refuses to declare one without
 * `contacts:read_all`. The loads pass that entitlement through (`canFirmWide`) so the control
 * isn't presented as working to someone it will 403 — and when it is bypassed, the API's own reason
 * is what the form shows.
 */

import { describe, expect, it, vi } from 'vitest';
import { load as loadList } from '../+page.server';
import { actions as detailActions, load as loadDetail } from './[uid]/+page.server';
import { actions as newActions, load as loadNew } from './new/+page.server';

type Json = Record<string, unknown> | unknown[];

/** A fetch stub returning one canned response, plus the calls it recorded.
 *
 *  `only` scopes that response to URLs containing it, answering everything else with an empty 200.
 *  The datasets LISTING is now its own route. This file still drives the `/templates` loader's
 *  datasets slice (the create wizard's dataset step), so the stub scopes responses to `/datasets`
 *  — without that, a documents-kind load would also fetch templates and forms. */
function stubFetch(status: number, body: Json | null, only?: string) {
	const calls: { url: string; init?: RequestInit }[] = [];
	const fetch = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
		const u = String(url);
		calls.push({ url: u, init });
		if (only && !u.includes(only)) {
			return new Response('[]', { status: 200, headers: { 'content-type': 'application/json' } });
		}
		return new Response(body === null ? null : JSON.stringify(body), {
			status,
			headers: { 'content-type': 'application/json' }
		});
	});
	return { fetch: fetch as unknown as typeof globalThis.fetch, calls };
}

function locals(permissions: string[]) {
	return {
		user: { email: 'staff@firm.test' },
		role: 'adviser',
		permissions,
		accessToken: 'token'
	};
}

const READ_WRITE = ['datasets:read', 'datasets:write'];

function listEvent(status: number, body: Json | null, permissions = READ_WRITE) {
	const { fetch, calls } = stubFetch(status, body, '/datasets');
	return { event: { locals: locals(permissions), fetch }, calls };
}

function detailEvent(status: number, body: Json | null, form: Record<string, string>) {
	const { fetch, calls } = stubFetch(status, body);
	return {
		event: {
			params: { uid: 'ds_1' },
			locals: locals(READ_WRITE),
			fetch,
			request: new Request('http://portal.test/datasets/ds_1', {
				method: 'POST',
				body: new URLSearchParams(form)
			})
		},
		calls
	};
}

type ListArg = Parameters<typeof loadList>[0];
// load() can also redirect, so its return type is `void | …`; the tests assert on the data branch.
type ListData = {
	datasets: { code: string }[];
	canManageDatasets: boolean;
	loadError: boolean;
};
/** A `url` is supplied for every case: the loader reads `?q` from it, because the search is the
 *  SERVER's question now rather than browser state. A real load event always carries one — these
 *  cases simply predate the loader needing it — and omitting it threw before any assertion could
 *  run, which reads as the loader being broken rather than the harness. */
const list = async (event: Record<string, unknown>): Promise<ListData> =>
	(await loadList({
		url: new URL('http://portal.test/datasets'),
		...event
	} as ListArg)) as unknown as ListData;
type ActionArg<K extends keyof typeof detailActions> = Parameters<
	NonNullable<(typeof detailActions)[K]>
>[0];

describe('/templates — the datasets slice', () => {
	it('reports a failed fetch instead of rendering an empty catalogue', async () => {
		// 403 is deterministic, so fetchJson fails fast rather than retrying.
		const { event } = listEvent(403, { detail: 'nope' });
		const data = await list(event);
		expect(data.loadError).toBe(true);
		expect(data.datasets).toEqual([]);
	});

	it('does not call the API at all without datasets:read', async () => {
		const { event, calls } = listEvent(200, []);
		const data = await list({ ...event, locals: locals(['documents:read']) });
		expect(calls).toHaveLength(0);
		expect(data).toMatchObject({ datasets: [], canManageDatasets: false, loadError: false });
	});

	it('maps the API rows and derives write access', async () => {
		const { event } = listEvent(200, [
			{
				uid: 'ds_1',
				code: 'isa_limits',
				name: 'ISA limits',
				description: null,
				format: 'inline',
				liveness: 'static',
				scope: 'global',
				enabled: true,
				version_count: 1,
				published_version_number: 1,
				created_at: '2026-07-01T00:00:00Z',
				created_by: 'Ada Staff'
			}
		]);
		const data = await list(event);
		expect(data.loadError).toBe(false);
		expect(data.canManageDatasets).toBe(true);
		expect(data.datasets.map((d) => d.code)).toEqual(['isa_limits']);
	});
});

describe('run query', () => {
	it('surfaces the API reason when the query is refused', async () => {
		const { event } = detailEvent(
			422,
			{ detail: 'A dataset query may only read. Not allowed here: set_config.' },
			{}
		);
		const result = await detailActions.runQuery!(event as unknown as ActionArg<'runQuery'>);
		// An ActionFailure: { status, data } — the page renders data.runError in an Alert.
		expect(result).toMatchObject({
			status: 422,
			data: { runError: 'A dataset query may only read. Not allowed here: set_config.' }
		});
	});

	it('falls back to a plain message when the API sends no detail', async () => {
		const { event } = detailEvent(500, null, {});
		const result = await detailActions.runQuery!(event as unknown as ActionArg<'runQuery'>);
		expect(result).toMatchObject({ status: 500, data: { runError: 'The query didn’t run.' } });
	});

	it('passes the chosen client through and keeps the truncation notice', async () => {
		const { event, calls } = detailEvent(
			200,
			{
				columns: ['id'],
				rows: [[1]],
				truncated: true,
				row_limit: 5000,
				notice: "Dataset 'big' returned more than 5000 rows; only the first were used."
			},
			{ contact_id: '42' }
		);
		const result = (await detailActions.runQuery!(event as unknown as ActionArg<'runQuery'>)) as {
			run: { truncated: boolean; rowLimit: number; notice: string | null };
		};
		expect(calls[0].url).toContain('contact_id=42');
		expect(result.run.truncated).toBe(true);
		expect(result.run.rowLimit).toBe(5000);
		expect(result.run.notice).toContain('more than 5000 rows');
	});
});

describe('details and delete', () => {
	it('repeats the in-use count when deletion is refused', async () => {
		const { event } = detailEvent(
			409,
			{ detail: '2 template(s) still use this dataset. Unlink it from them first, or disable it.' },
			{}
		);
		const result = await detailActions.delete!(event as unknown as ActionArg<'delete'>);
		expect(result).toMatchObject({ status: 409 });
		expect((result as { data: { error: string } }).data.error).toContain('2 template(s)');
	});

	it('omits scope when the form has none (a non-SQL dataset offers no scope control)', async () => {
		const { event, calls } = detailEvent(200, { uid: 'ds_1' }, { name: 'Renamed' });
		await detailActions.updateDetails!(event as unknown as ActionArg<'updateDetails'>);
		const body = JSON.parse(String(calls[0].init?.body));
		expect(body).toMatchObject({ name: 'Renamed', enabled: false });
		expect('scope' in body).toBe(false);
	});

	it('sends scope when the SQL scope control was rendered', async () => {
		const { event, calls } = detailEvent(
			200,
			{ uid: 'ds_1' },
			{ name: 'Renamed', scope: 'subject', enabled: 'on' }
		);
		await detailActions.updateDetails!(event as unknown as ActionArg<'updateDetails'>);
		const body = JSON.parse(String(calls[0].init?.body));
		expect(body).toMatchObject({ name: 'Renamed', scope: 'subject', enabled: true });
	});

	it('passes the firm-wide-SQL entitlement to the page so Scope offers only what works', async () => {
		// The detail load makes three calls (dataset, versions, subjects), so the stub answers by URL.
		const fetch = vi.fn(async (url: string | URL) => {
			const body = String(url).endsWith(`/datasets/ds_1`)
				? { uid: 'ds_1', code: 'x', name: 'X', scope: 'subject', format: 'sql' }
				: [];
			return new Response(JSON.stringify(body), {
				status: 200,
				headers: { 'content-type': 'application/json' }
			});
		}) as unknown as typeof globalThis.fetch;
		const event = (permissions: string[]) => ({
			params: { uid: 'ds_1' },
			locals: locals(permissions),
			fetch
		});
		type DetailData = { canFirmWide: boolean };
		const scoped = (await loadDetail(
			event(READ_WRITE) as unknown as Parameters<typeof loadDetail>[0]
		)) as unknown as DetailData;
		const fullBook = (await loadDetail(
			event([...READ_WRITE, 'contacts:read_all']) as unknown as Parameters<typeof loadDetail>[0]
		)) as unknown as DetailData;
		expect(scoped.canFirmWide).toBe(false);
		expect(fullBook.canFirmWide).toBe(true);
	});

	it('refuses a nameless rename before calling the API', async () => {
		const { event, calls } = detailEvent(200, { uid: 'ds_1' }, { name: '  ' });
		const result = await detailActions.updateDetails!(
			event as unknown as ActionArg<'updateDetails'>
		);
		expect(result).toMatchObject({ status: 400, data: { error: 'Name is required.' } });
		expect(calls).toHaveLength(0);
	});
});

describe('new dataset', () => {
	it('offers firm-wide scope only to an author who may declare it', async () => {
		const { fetch } = stubFetch(200, {});
		type NewData = { canFirmWide: boolean };
		const scoped = (await loadNew({
			locals: locals(READ_WRITE),
			fetch
		} as unknown as Parameters<typeof loadNew>[0])) as unknown as NewData;
		const fullBook = (await loadNew({
			locals: locals([...READ_WRITE, 'contacts:read_all']),
			fetch
		} as unknown as Parameters<typeof loadNew>[0])) as unknown as NewData;
		// The Scope select is built from this: false → per-client only, with the reason stated.
		expect(scoped.canFirmWide).toBe(false);
		expect(fullBook.canFirmWide).toBe(true);
	});

	it("surfaces the API's reason when a firm-wide SQL dataset is refused, and keeps the form", async () => {
		// The UI hides the option, but the guard that matters is the API's — a hand-rolled POST, an
		// older cached page or a scripted client all land here, and the author must be told why.
		const { fetch, calls } = stubFetch(403, {
			detail:
				"A firm-wide (global) SQL dataset is run across the whole firm's data when a document is " +
				'generated, so declaring one needs the ‘Read all clients’ permission (contacts:read_all).'
		});
		const event = {
			locals: locals(READ_WRITE),
			fetch,
			request: new Request('http://portal.test/datasets/new', {
				method: 'POST',
				body: new URLSearchParams({
					code: 'firm_book',
					name: 'Firm book',
					format: 'sql',
					scope: 'global',
					sql_query: 'SELECT id, email FROM crm.contacts'
				})
			})
		};
		const result = (await newActions.default!(
			event as unknown as Parameters<NonNullable<typeof newActions.default>>[0]
		)) as { status: number; data: { error: string; values: { scope: string } } };
		expect(JSON.parse(String(calls[0].init?.body))).toMatchObject({ scope: 'global' });
		expect(result.status).toBe(403);
		expect(result.data.error).toContain('Ask an administrator');
		expect(result.data.values.scope).toBe('global'); // the form comes back filled in
	});
});
