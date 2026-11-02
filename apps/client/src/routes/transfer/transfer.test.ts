/**
 * The deposit/withdraw page (`[kind]/+page.server.ts`): the loader and the three actions.
 *
 * `[kind]` is a bare path parameter, so anything at all reaches this loader — the guard that turns
 * everything but deposit/withdraw into a 404 is the only thing stopping /transfer/anything
 * rendering a page titled after the URL. The accounts list is the client's REAL accounts (the picker
 * used to be driven off `$lib/demo`'s invented ISA), and a load failure must surface as a banner
 * rather than as "no accounts yet".
 *
 * The actions hold NO money logic: `preview` shows what `/crm/money/preview` said, `confirm` posts
 * to `/crm/money/instructions` and the API decides again, `cancel` posts to the request's own
 * cancel door. What is pinned here is the wiring — which URL, what body, what a failure returns to.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { actions, load } from './[kind]/+page.server';

type Account = {
	id: number;
	uid: string;
	name: string | null;
	value: number;
	service_type?: string | null;
	status?: string | null;
};
type Adviser = { name: string; firm: string; email: string; phone: string; kind?: string };

const ACCOUNTS: Account[] = [{ id: 3, uid: 'acc3', name: 'Stocks & Shares ISA', value: 24_500 }];
const ADVISER: Adviser = {
	name: 'Priya Raman',
	firm: 'Ashworth Wealth',
	email: 'priya@ashworth.test',
	phone: '0117 555 0100'
};
/** The firm as the contact when nobody is assigned ($lib/adviser::firmContact). */
const FIRM = {
	kind: 'firm',
	name: 'Ashworth Wealth',
	firm: 'Ashworth Wealth',
	email: 'help@ashworth.test',
	phone: '',
	photoUrl: 'https://cdn.test/ashworth.png'
};
const PENDING = [
	{
		uid: 'ins1',
		kind: 'expected_in',
		amount: 500,
		status: 'pending',
		due_date: '2026-10-06',
		created_at: '2026-10-01T10:00:00Z',
		account_uid: 'acc3',
		account_name: 'Stocks & Shares ISA',
		review_required: false,
		raised_by_client: true
	}
];
const PREVIEW = {
	kind: 'deposit',
	amount: 500,
	instruction_kind: 'expected_in',
	refusal: null,
	review_required: false,
	flags: [],
	account: { uid: 'acc3', name: 'Stocks & Shares ISA' }
};

/** `/crm/accounts` answers `body`; `/crm/context` answers the adviser (null ⇒ none assigned,
 *  a number ⇒ that failure); `/crm/money/instructions` (GET) answers `pending`. Only the
 *  ACCOUNTS calls are recorded — they are what the tests about the picker count and inspect. */
function stubFetch(
	body: Account[] | number,
	adviser: Adviser | null | number = ADVISER,
	pending: unknown[] | number = []
) {
	const calls: string[] = [];
	const fetch = vi.fn(async (input: RequestInfo | URL) => {
		const url = String(input);
		if (url.includes('/crm/context')) {
			return typeof adviser === 'number'
				? ({ ok: false, status: adviser } as unknown as Response)
				: ({ ok: true, status: 200, json: async () => ({ adviser }) } as unknown as Response);
		}
		if (url.includes('/crm/money/actions')) {
			return {
				ok: true,
				status: 200,
				json: async () => ({ disabled: [] })
			} as unknown as Response;
		}
		if (url.includes('/crm/money/instructions')) {
			return typeof pending === 'number'
				? ({ ok: false, status: pending } as unknown as Response)
				: ({ ok: true, status: 200, json: async () => pending } as unknown as Response);
		}
		calls.push(url);
		return typeof body === 'number'
			? ({ ok: false, status: body } as unknown as Response)
			: ({ ok: true, status: 200, json: async () => body } as unknown as Response);
	});
	return { fetch: fetch as unknown as typeof globalThis.fetch, calls };
}

type LoadArg = Parameters<typeof load>[0];

const LOCALS = {
	user: { id: 'u-me', email: 'jo@example.test' },
	accessToken: 'tok',
	classification: { slug: 'd2c', capabilities: { money_movement: true } },
	branding: {
		icon_url: 'https://cdn.test/ashworth.png',
		support: { email: 'help@ashworth.test' }
	},
	appName: 'Ashworth Wealth'
};

const run = (
	kind: string,
	fetch: typeof globalThis.fetch,
	search = '',
	activeContactId: number | null = null
) =>
	load({
		locals: LOCALS,
		params: { kind },
		url: new URL(`http://portal.test/transfer/${kind}${search}`),
		parent: async () => ({ activeContactId }),
		fetch
	} as unknown as LoadArg) as Promise<{
		kind: string;
		routeKind: string;
		accounts: Account[];
		contact: Adviser;
		preselected: string | null;
		pending: unknown[];
		loadError: boolean;
	}>;

describe('kind guard', () => {
	it('accepts the two real kinds, translating the route word to the API word', async () => {
		const { fetch } = stubFetch(ACCOUNTS);
		await expect(run('deposit', fetch)).resolves.toMatchObject({
			kind: 'deposit',
			routeKind: 'deposit'
		});
		await expect(run('withdraw', fetch)).resolves.toMatchObject({
			kind: 'withdrawal',
			routeKind: 'withdraw'
		});
	});

	it('404s a read-only book with no execution-only account', async () => {
		const { fetch, calls } = stubFetch(ACCOUNTS);
		await expect(
			load({
				locals: {
					user: { id: 'u-me' },
					accessToken: 'tok',
					classification: { slug: 'advised', capabilities: { money_movement: false } }
				},
				params: { kind: 'withdraw' },
				url: new URL('http://portal.test/transfer/withdraw'),
				parent: async () => ({ activeContactId: null }),
				fetch
			} as unknown as LoadArg)
		).rejects.toMatchObject({ status: 404 });
		expect(calls).toHaveLength(1);
	});

	it('keeps an execution-only account available inside an advised book', async () => {
		const mixed = [
			{ id: 1, uid: 'a1', name: 'Advised GIA', value: 10, service_type: 'advised' },
			{ id: 2, uid: 'a2', name: 'Execution-only ISA', value: 20, service_type: 'execution_only' }
		];
		const { fetch } = stubFetch(mixed);
		const data = (await load({
			locals: {
				user: { id: 'u-me' },
				accessToken: 'tok',
				classification: { slug: 'advised', capabilities: { money_movement: false } }
			},
			params: { kind: 'deposit' },
			url: new URL('http://portal.test/transfer/deposit'),
			parent: async () => ({ activeContactId: null }),
			fetch
		} as unknown as LoadArg)) as { accounts: Account[] };
		expect(data.accounts).toEqual([mixed[1]]);
	});

	it('404s anything else instead of rendering a page named after the URL', async () => {
		for (const kind of ['transfer', 'DEPOSIT', 'withdraw-all', '../admin', '', 'withdrawal']) {
			const { fetch, calls } = stubFetch(ACCOUNTS);
			await expect(run(kind, fetch)).rejects.toMatchObject({ status: 404 });
			// Fails closed before the API is touched.
			expect(calls).toEqual([]);
		}
	});
});

describe('accounts', () => {
	it("returns the client's own accounts from the RLS-scoped endpoint", async () => {
		const { fetch, calls } = stubFetch(ACCOUNTS, ADVISER, PENDING);
		const data = await run('deposit', fetch);
		expect(data).toEqual({
			kind: 'deposit',
			routeKind: 'deposit',
			accounts: ACCOUNTS,
			contact: ADVISER,
			preselected: 'acc3',
			pending: PENDING,
			loadError: false
		});
		expect(calls[0]).toMatch(/\/crm\/accounts$/);
	});

	it('leaves a DRAFT off the picker — nothing else in the portal lists one', async () => {
		// An application in progress has no name (it showed as its product, "Seccl GIA £0.00")
		// and nothing to pay into; the home and accounts pages never listed it, so this page
		// must not either — whether or not the book may move money.
		const withDraft = [
			...ACCOUNTS,
			{ id: 9, uid: 'a9', name: null, value: 0, status: 'draft', service_type: 'execution_only' }
		];
		const { fetch } = stubFetch(withDraft);
		expect((await run('deposit', fetch)).accounts).toEqual(ACCOUNTS);
	});

	it('flags a load failure instead of passing an empty list off as "no accounts yet"', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const { fetch } = stubFetch(500);
		const data = await run('withdraw', fetch);
		expect(data).toMatchObject({ kind: 'withdrawal', accounts: [], loadError: true });
	});

	it('an empty list is a legitimate answer, not an error', async () => {
		const { fetch } = stubFetch([]);
		const data = await run('deposit', fetch);
		expect(data).toMatchObject({ accounts: [], preselected: null, loadError: false });
	});

	it('a failed pending-list read is an empty list, not a failed page', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const { fetch } = stubFetch(ACCOUNTS, ADVISER, 503);
		const data = await run('deposit', fetch);
		expect(data.pending).toEqual([]);
		expect(data.loadError).toBe(false);
	});
});

describe('the preselected account', () => {
	const two = [
		...ACCOUNTS,
		{ id: 4, uid: 'acc4', name: 'GIA', value: 1_000, service_type: 'execution_only' }
	];

	it('honours `?account=` when it names an eligible account', async () => {
		const { fetch } = stubFetch(two);
		expect((await run('deposit', fetch, '?account=acc4')).preselected).toBe('acc4');
	});

	it('ignores an unknown or ineligible uid rather than refusing the page', async () => {
		const { fetch } = stubFetch(two);
		expect((await run('deposit', fetch, '?account=nope')).preselected).toBeNull();
		expect((await run('deposit', fetch, '?account=a/../b')).preselected).toBeNull();
	});

	it('picks the only account when there is just one', async () => {
		const { fetch } = stubFetch(ACCOUNTS);
		expect((await run('deposit', fetch)).preselected).toBe('acc3');
	});
});

describe('who to contact', () => {
	it('names the adviser when one is assigned', async () => {
		const { fetch } = stubFetch(ACCOUNTS, ADVISER);
		expect((await run('deposit', fetch)).contact).toEqual(ADVISER);
	});

	it('names the FIRM when nobody is assigned — a direct client has no adviser to contact', async () => {
		const { fetch } = stubFetch(ACCOUNTS, null);
		expect((await run('deposit', fetch)).contact).toEqual(FIRM);
	});

	it('falls back to the firm when the context read fails, rather than failing the page', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const { fetch } = stubFetch(ACCOUNTS, 503);
		const data = await run('deposit', fetch);
		expect(data.contact).toEqual(FIRM);
		expect(data.loadError).toBe(false);
	});

	it('the page says "{firm}" for the firm and "your adviser" for a person', () => {
		const source = readFileSync(join(import.meta.dirname, '[kind]', '+page.svelte'), 'utf8');
		expect(source).toContain("data.contact.kind === 'firm' ? data.contact.name : 'your adviser'");
		expect(source).toContain('Contact {whom}');
		expect(source).toContain('message {whom}');
		expect(source).not.toContain('Contact your adviser');
	});

	it('prints standalone figures through Money, so the pence step back', () => {
		const source = readFileSync(join(import.meta.dirname, '[kind]', '+page.svelte'), 'utf8');
		expect(source).toContain('<Money amount={account.value ?? 0} />');
		expect(source).toContain('{ amount: preview.amount }');
		expect(source).toContain('<Money amount={preview.value_after} />');
		// Only the button wording and the sentences keep plain text.
		expect(source).not.toContain('formatGBP(preview');
		expect(source).not.toContain('money(');
	});
});

describe('the active client', () => {
	it('narrows the account picker to the client on screen', async () => {
		// `contact_scope()` is every client this login acts for, so the un-narrowed call offered
		// another person's accounts under the heading of the one the nav said you were viewing.
		const h = stubFetch([]);
		await run('deposit', h.fetch, '', 9);
		expect(h.calls[0]).toContain('contact_id=9');
	});

	it('asks for nothing in particular when there is no active client', async () => {
		const h = stubFetch([]);
		await run('deposit', h.fetch);
		expect(h.calls[0]).not.toContain('contact_id');
	});
});

// ---------------------------------------------------------------------------------------------
// Actions

type Hit = { url: string; method: string; body: unknown };

/** A fetch that records every POST and answers them in order from `answers`. */
function postFetch(...answers: ({ status: number; body?: unknown } | undefined)[]) {
	const hits: Hit[] = [];
	let i = 0;
	const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
		const url = String(input);
		hits.push({
			url,
			method: init?.method ?? 'GET',
			body: typeof init?.body === 'string' ? JSON.parse(init.body) : null
		});
		const a = answers[i++] ?? { status: 200, body: {} };
		return {
			ok: a.status < 400,
			status: a.status,
			json: async () => a.body ?? {}
		} as unknown as Response;
	});
	return { fetch: fetch as unknown as typeof globalThis.fetch, hits };
}

const form = (fields: Record<string, string>) => {
	const fd = new FormData();
	for (const [k, v] of Object.entries(fields)) fd.set(k, v);
	return fd;
};

type ActionArg = Parameters<NonNullable<typeof actions.preview>>[0];

const act = (
	name: 'preview' | 'confirm' | 'cancel',
	fields: Record<string, string>,
	fetch: typeof globalThis.fetch,
	kind = 'deposit',
	locals: Record<string, unknown> = LOCALS
) =>
	actions[name]!({
		request: { formData: async () => form(fields) },
		locals,
		params: { kind },
		fetch
	} as unknown as ActionArg) as Promise<
		Record<string, unknown> & { status?: number; data?: Record<string, unknown> }
	>;

describe('preview', () => {
	it('asks the API for the preview with the API kind and a numeric amount, and answers the review step', async () => {
		const { fetch, hits } = postFetch({ status: 200, body: PREVIEW });
		const out = await act('preview', { account: 'acc3', amount: '£1,500.00' }, fetch, 'withdraw');
		expect(hits).toHaveLength(1);
		expect(hits[0]).toMatchObject({
			url: expect.stringMatching(/\/crm\/money\/preview$/),
			method: 'POST',
			body: { kind: 'withdrawal', account_uid: 'acc3', amount: 1500 }
		});
		expect(out).toMatchObject({
			step: 'review',
			preview: PREVIEW,
			account_uid: 'acc3',
			amount: 1500
		});
	});

	it("returns a refusal to the amount step in the API's own words, with the choices kept", async () => {
		const refused = {
			...PREVIEW,
			refusal: 'Only £870 can be withdrawn from this account just now.'
		};
		const { fetch } = postFetch({ status: 200, body: refused });
		const out = await act('preview', { account: 'acc3', amount: '2500' }, fetch, 'withdraw');
		expect(out.status).toBe(400);
		expect(out.data).toMatchObject({
			error: 'Only £870 can be withdrawn from this account just now.',
			account_uid: 'acc3',
			amount: 2500
		});
		expect(out.data).not.toHaveProperty('step');
	});

	it('refuses a bad amount or a missing account before touching the API', async () => {
		for (const fields of [
			{ account: 'acc3', amount: '0' },
			{ account: 'acc3', amount: '-5' },
			{ account: 'acc3', amount: 'lots' },
			{ account: '', amount: '10' },
			{ account: 'a/../b', amount: '10' }
		]) {
			const { fetch, hits } = postFetch();
			const out = await act('preview', fields, fetch);
			expect(out.status).toBe(400);
			expect(hits).toEqual([]);
		}
	});

	it("surfaces the API's deliberate guidance, not its diagnostics", async () => {
		const { fetch } = postFetch({
			status: 400,
			body: { detail: 'No money can be moved on this account yet.' }
		});
		const out = await act('preview', { account: 'acc3', amount: '10' }, fetch);
		expect(out.status).toBe(400);
		expect(out.data?.error).toBe('No money can be moved on this account yet.');
	});

	it('404s an unknown kind and 401s a signed-out caller', async () => {
		const { fetch, hits } = postFetch();
		expect((await act('preview', { account: 'acc3', amount: '10' }, fetch, 'nope')).status).toBe(
			404
		);
		expect(
			(await act('preview', { account: 'acc3', amount: '10' }, fetch, 'deposit', { user: null }))
				.status
		).toBe(401);
		expect(hits).toEqual([]);
	});
});

describe('confirm', () => {
	const RAISED = {
		instruction: { uid: 'ins9', kind: 'expected_in', amount: 500, status: 'pending' },
		review_required: false,
		held: false,
		flags: [],
		preview: PREVIEW,
		pay_in: null
	};

	it('raises through the API only once the declaration is ticked, and answers the done step', async () => {
		const { fetch, hits } = postFetch({ status: 201, body: RAISED });
		const out = await act('confirm', { account: 'acc3', amount: '500', understood: 'on' }, fetch);
		expect(hits).toHaveLength(1);
		expect(hits[0]).toMatchObject({
			url: expect.stringMatching(/\/crm\/money\/instructions$/),
			method: 'POST',
			body: { kind: 'deposit', account_uid: 'acc3', amount: 500 }
		});
		expect(out).toEqual({ step: 'done', raised: RAISED });
	});

	it('an unticked declaration re-previews and returns to the review step — never raises', async () => {
		const { fetch, hits } = postFetch({ status: 200, body: PREVIEW });
		const out = await act('confirm', { account: 'acc3', amount: '500' }, fetch);
		expect(hits).toHaveLength(1);
		expect(hits[0].url).toMatch(/\/crm\/money\/preview$/);
		expect(out.status).toBe(400);
		expect(out.data).toMatchObject({
			step: 'review',
			preview: PREVIEW,
			error: 'Please confirm you’ve read this and want to go ahead.'
		});
	});

	it("returns the API's refusal to the amount step when the account's position has moved", async () => {
		const { fetch } = postFetch({
			status: 400,
			body: { detail: 'Only £400 can be withdrawn from this account just now.' }
		});
		const out = await act(
			'confirm',
			{ account: 'acc3', amount: '500', understood: 'on' },
			fetch,
			'withdraw'
		);
		expect(out.status).toBe(400);
		expect(out.data).toMatchObject({
			error: 'Only £400 can be withdrawn from this account just now.',
			account_uid: 'acc3',
			amount: 500
		});
		expect(out.data).not.toHaveProperty('step');
	});
});

describe('cancel', () => {
	it("posts to the request's own cancel door", async () => {
		const { fetch, hits } = postFetch({ status: 200, body: { ok: true } });
		const out = await act('cancel', { uid: 'ins1' }, fetch);
		expect(hits[0]).toMatchObject({
			url: expect.stringMatching(/\/crm\/money\/instructions\/ins1\/cancel$/),
			method: 'POST'
		});
		expect(out).toEqual({ cancelled: 'ins1' });
	});

	it('refuses a uid that could escape its path segment, before touching the API', async () => {
		const { fetch, hits } = postFetch();
		const out = await act('cancel', { uid: 'x/../../messaging/send?' }, fetch);
		expect(out.status).toBe(400);
		expect(hits).toEqual([]);
	});

	it("passes on the API's 409 when the request is no longer pending", async () => {
		const { fetch } = postFetch({
			status: 409,
			body: { detail: 'This request is no longer pending.' }
		});
		const out = await act('cancel', { uid: 'ins1' }, fetch);
		expect(out.status).toBe(409);
		expect(out.data?.error).toBe('This request is no longer pending.');
	});
});
