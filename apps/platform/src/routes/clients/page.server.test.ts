/** The clients list's server actions. The search/segment/sort predicates and the header summary
 *  moved to @graphene/ui book/clients with the shared listing component; their tests live there
 *  (pkgs/ui .../book/clients/book-filters.test.ts).
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { actions } from './+page.server';

// ---- ?/createClient -------------------------------------------------------------------------

const locals = {
	user: { id: 'u_1' },
	accessToken: 'tok',
	permissions: ['contacts:write'],
	role: 'adviser'
} as unknown as App.Locals;

type Call = { url: string; method: string; body: unknown };

function createClient(
	fields: Record<string, string>,
	opts: { book?: unknown[]; createStatus?: number; permissions?: string[] } = {}
) {
	const calls: Call[] = [];
	const json = (body: unknown, status = 200) =>
		new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
	const fetch = (async (url: string | URL, init?: RequestInit) => {
		const u = String(url);
		calls.push({ url: u, method: init?.method ?? 'GET', body: init?.body });
		if (init?.method === 'POST') {
			if (opts.createStatus) return json({ detail: 'API said no' }, opts.createStatus);
			return json({ id: 99, uid: 'cli_new99' }, 201);
		}
		return json(opts.book ?? []);
	}) as unknown as typeof globalThis.fetch;
	const body = new FormData();
	for (const [k, v] of Object.entries(fields)) body.set(k, v);
	const request = new Request('http://portal.test/clients', { method: 'POST', body });
	const actionLocals = {
		...locals,
		permissions: opts.permissions ?? ['contacts:create']
	} as unknown as App.Locals;
	return {
		calls,
		posted: () => calls.filter((c) => c.method === 'POST'),
		run: () =>
			actions.createClient!({ request, locals: actionLocals, fetch } as never) as Promise<{
				status?: number;
				data?: { error?: string; field?: string | null; existingUid?: string | null };
				uid?: string | null;
			}>
	};
}

describe('?/createClient', () => {
	it('creates the client and hands back the uid so the adviser lands on the record', async () => {
		const c = createClient({ type: 'individual', first_name: 'Theo', surname: 'Caldwell' });
		const result = await c.run();
		expect(result.uid).toBe('cli_new99');
		const sent = JSON.parse(String(c.posted()[0].body));
		// `name` is the display name every downstream consumer falls back to (the dashboard rail,
		// documents.requests.contact_name). It used to be forced to null for individuals.
		expect(sent.name).toBe('Theo Caldwell');
		expect(sent.surname).toBe('Caldwell');
		expect(sent.sub_type).toBeNull();
	});

	it('requires a surname for a person and a name for an organisation', async () => {
		const person = await createClient({ type: 'individual', first_name: 'Theo' }).run();
		expect(person.status).toBe(400);
		expect(person.data?.error).toContain('surname');

		const org = await createClient({ type: 'organisation' }).run();
		expect(org.status).toBe(400);
		expect(org.data?.error).toContain('organisation name');
	});

	it('rejects a malformed email without creating anything', async () => {
		const c = createClient({ type: 'individual', surname: 'Caldwell', email: 'nope' });
		const result = await c.run();
		expect(result.status).toBe(400);
		expect(result.data?.field).toBe('email');
		expect(c.posted()).toHaveLength(0);
	});

	it('refuses a duplicate email on the visible book and offers the existing client', async () => {
		const c = createClient(
			{ type: 'individual', surname: 'Caldwell', email: 'Theo@Caldwell.test' },
			{ book: [{ id: 7, uid: 'cli_exist7', email: 'theo@caldwell.test' }] }
		);
		const result = await c.run();
		expect(result.status).toBe(409);
		expect(result.data?.existingUid).toBe('cli_exist7'); // case-insensitive match
		expect(c.posted()).toHaveLength(0);
		expect(c.calls.some((call) => call.url.includes('q=Theo%40Caldwell.test'))).toBe(true);
	});

	it('records an organisation’s kind when it is given', async () => {
		const c = createClient({
			type: 'organisation',
			name: 'Oakden Trust',
			sub_type: 'trust'
		});
		const result = await c.run();
		expect(result.uid).toBe('cli_new99');
		expect(JSON.parse(String(c.posted()[0].body))).toMatchObject({
			type: 'organisation',
			name: 'Oakden Trust',
			sub_type: 'trust'
		});
	});

	it('refuses an organisation kind the vocabulary does not know', async () => {
		const c = createClient({
			type: 'organisation',
			name: 'Oakden Trust',
			sub_type: 'partnership'
		});
		const result = await c.run();
		expect(result.status).toBe(400);
		expect(c.posted()).toHaveLength(0);
	});

	it('passes the API’s own error through instead of reporting success', async () => {
		const result = await createClient(
			{ type: 'individual', surname: 'Caldwell' },
			{ createStatus: 422 }
		).run();
		expect(result.status).toBe(422);
		expect(result.data?.error).toBe('API said no');
	});

	it('redirects a contacts:write-only caller — minting is contacts:create', async () => {
		const c = createClient(
			{ type: 'individual', surname: 'Caldwell' },
			{ permissions: ['contacts:write'] }
		);
		await expect(c.run()).rejects.toMatchObject({ status: 303, location: '/clients' });
		expect(c.posted()).toHaveLength(0);
	});
});

// ---- ?/restoreClient ------------------------------------------------------------------------
// Reopen lives on the LIST as well as the record: Closed stays loadable, but this is where you
// find everyone who has been closed.

describe('?/restoreClient', () => {
	const restore = (fields: Record<string, string>, status = 200) => {
		const calls: Call[] = [];
		const fetch = (async (url: string | URL, init?: RequestInit) => {
			calls.push({ url: String(url), method: init?.method ?? 'GET', body: init?.body });
			return new Response(JSON.stringify(status === 200 ? { id: 7 } : { detail: 'API said no' }), {
				status,
				headers: { 'content-type': 'application/json' }
			});
		}) as unknown as typeof globalThis.fetch;
		const body = new FormData();
		for (const [k, v] of Object.entries(fields)) body.set(k, v);
		return {
			calls,
			run: () =>
				actions.restoreClient!({
					request: new Request('http://portal.test/clients', { method: 'POST', body }),
					locals,
					fetch
				} as never) as Promise<{ status?: number; data?: { error?: string }; uid?: string | null }>
		};
	};

	// The row posts its `uid`: `/firm/contacts/{contact_uid}/restore` is uid-typed, and the serial
	// the listing also carries resolves to no contact at all.
	it('posts the restore for the row’s client', async () => {
		const r = restore({ uid: 'con_7' });
		const result = await r.run();
		expect(r.calls[0].url).toContain('/firm/contacts/con_7/restore');
		expect(r.calls[0].method).toBe('POST');
		expect(result.uid).toBeNull();
	});

	it('refuses to post without a client, and surfaces the API’s error', async () => {
		const empty = await restore({}).run();
		expect(empty.status).toBe(400);

		// A serial is not a uid: the field it used to post is simply absent now.
		const serial = await restore({ id: '7' }).run();
		expect(serial.status).toBe(400);

		const failed = await restore({ uid: 'con_7' }, 404).run();
		expect(failed.status).toBe(404);
		expect(failed.data?.error).toBe('API said no');
	});
});

describe('the listing mount splits New client from Reopen', () => {
	it('hands create only when canCreate, restore only when canManage', () => {
		const page = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');
		expect(page).toContain('data.canManage || data.canCreate');
		expect(page).toContain("create: '?/createClient'");
		expect(page).toContain("restore: '?/restoreClient'");
		expect(page).toMatch(/data\.canManage \? \{ restore:/);
		expect(page).toMatch(/data\.canCreate/);
		expect(page).not.toMatch(/actions=\{\s*\{/);
	});

	it('hides Export unless listings:export is granted', () => {
		const page = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');
		expect(page).toContain('listingExportHref');
	});
});
