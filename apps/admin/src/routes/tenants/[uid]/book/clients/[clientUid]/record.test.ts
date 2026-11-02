/**
 * The book client-record loader: `?tenant=` on every read, API refusals propagated as page
 * errors (not blank records), and the platform's allocation derivation reproduced — the ring is
 * scaled to the client's AUM with the shared `classifyHolding` buckets.
 *
 * Two postures share it: `book:read` alone loads the read-only record (and must NOT touch the
 * instructions endpoint), while `book:write` also loads the instructions that feed the
 * middle-office dialogs — degrading gracefully when an older API 403s them.
 */
import { describe, expect, it } from 'vitest';
import { load } from './+page.server';

const TENANT = { id: 4, uid: 't_9f2ab1', name: 'Caldwell Wealth' };
const locals = (permissions: string[] = ['book:read']) => ({
	user: { id: 'u-ops' },
	accessToken: 'tok',
	permissions,
	role: null
});
const parent = async () => ({ tenant: TENANT });

const DETAIL = {
	contact: {
		id: 5,
		uid: 'c_5',
		type: 'individual',
		service_type: 'advised',
		enabled: true,
		name: 'Theo Caldwell',
		title: null,
		first_name: 'Theo',
		surname: 'Caldwell',
		email: 't@caldwell.test',
		mobile: null,
		bank_account: { name: 'T Caldwell', sort_code: '112233', account_number: '12345678' }
	},
	advisers: [],
	accounts: [
		{
			id: 11,
			uid: 'a_11',
			name: 'GIA',
			status: 'active',
			value: 600,
			product_name: null,
			product_type: 'gia',
			model_id: null,
			model_name: null
		},
		{
			id: 12,
			uid: 'a_12',
			name: 'SIPP',
			status: 'active',
			value: 400,
			product_name: null,
			product_type: 'sipp',
			model_id: null,
			model_name: null
		}
	],
	notes: [],
	kyc: [{ kind: 'aml_check', status: 'passed', verified_at: '2026-07-01', valid_until: null }]
};

// 100 cash + 300 equities in lake terms, against an AUM of 1,000 ⇒ scale 2.5. Both legs sit on
// account 11, so the write-mode funding derivation has one basis to state.
const SUMMARY = {
	allocations: [
		{ label: 'Cash', value: 100 },
		{ label: 'Equities', value: 300 }
	],
	cash: 100,
	lake_total: 400,
	accounts: [{ account_id: 11, cash: 100, invested: 300, as_of: '2026-07-30' }]
};

const TASKS = [{ uid: 'tk_1', title: 'Chase ID', due_date: null, status: 'open' }];

// A pending withdrawal is a claim on account 11's cash leg; the settled row must not be.
const INSTRUCTIONS = [
	{
		uid: 'ins_1',
		kind: 'withdrawal',
		amount: 40,
		status: 'pending',
		due_date: null,
		note: null,
		created_at: '2026-07-01T00:00:00Z',
		account_id: 11,
		account_name: 'GIA',
		model_name: null
	},
	{
		uid: 'ins_2',
		kind: 'withdrawal',
		amount: 60,
		status: 'settled',
		due_date: null,
		note: null,
		created_at: '2026-06-01T00:00:00Z',
		account_id: 11,
		account_name: 'GIA',
		model_name: null
	}
];

function run(
	overrides: Partial<Record<'contact' | 'holdings' | 'tasks' | 'instructions', number>> = {},
	permissions: string[] = ['book:read']
) {
	const calls: URL[] = [];
	const fetch = (async (url: string | URL) => {
		const u = new URL(String(url));
		calls.push(u);
		const respond = (body: unknown, fail?: number) =>
			new Response(JSON.stringify(fail ? { detail: 'no' } : body), { status: fail ?? 200 });
		if (u.pathname === '/crm/contacts/con_5c1d') return respond(DETAIL, overrides.contact);
		if (u.pathname === '/crm/holdings/summary') return respond(SUMMARY, overrides.holdings);
		if (u.pathname === '/tasks') return respond(TASKS, overrides.tasks);
		if (u.pathname === '/firm/contacts/con_5c1d/instructions')
			return respond(INSTRUCTIONS, overrides.instructions);
		if (u.pathname === '/reviews/policies') return respond([]);
		throw new Error(`unexpected fetch: ${u}`);
	}) as unknown as typeof globalThis.fetch;
	return {
		calls,
		load: () =>
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			(load as any)({
				locals: locals(permissions),
				params: { uid: TENANT.uid, clientUid: 'con_5c1d' },
				fetch,
				parent
			})
	};
}

describe('the book client-record loader (read-only, book:read)', () => {
	it('targets the tenant on every read, and narrows holdings/tasks to the client', async () => {
		const { calls, load } = run();
		await load();
		expect(calls).toHaveLength(3);
		for (const u of calls) expect(u.searchParams.get('tenant')).toBe(TENANT.uid);
		const holdings = calls.find((u) => u.pathname === '/crm/holdings/summary');
		expect(holdings?.searchParams.get('contact_id')).toBe('5');
		const tasks = calls.find((u) => u.pathname === '/tasks');
		expect(tasks?.searchParams.get('assignee')).toBe('contact:5');
	});

	it('never touches the write-side endpoints, and states the read-only posture', async () => {
		const { calls, load } = run();
		const result = await load();
		// Absence of the reads — not a 403 — is the read-only contract: the fake fetch above throws
		// on any path it wasn't told about, so a stray instructions/model call fails the test too.
		expect(calls.map((u) => u.pathname).sort()).toEqual([
			'/crm/contacts/con_5c1d',
			'/crm/holdings/summary',
			'/tasks'
		]);
		expect(result.canWrite).toBe(false);
		expect(result.instructions).toEqual([]);
		expect(result.funding).toEqual([]);
	});

	it('scales the allocation ring to the client’s AUM (the platform derivation)', async () => {
		const result = await run().load();
		// AUM 1,000 over a lake total of 400 ⇒ scale 2.5; equities beat cash so they sort first.
		expect(result.allocations).toEqual([
			{ label: 'Equities', value: 750 },
			{ label: 'Cash', value: 250 }
		]);
		expect(result.cash).toBe(250);
		expect(result.bankAccount).toMatchObject({ sort_code: '112233' });
		expect(result.tasks).toEqual(TASKS);
	});

	it('propagates a 404 (unknown or archived client) as a page error', async () => {
		await expect(run({ contact: 404 }).load()).rejects.toMatchObject({ status: 404 });
	});

	it('propagates a 403 (book grant revoked mid-session) rather than rendering blank', async () => {
		await expect(run({ contact: 403 }).load()).rejects.toMatchObject({ status: 403 });
	});

	it('flags failed holdings and swallows failed tasks — partial record beats no record', async () => {
		const result = await run({ holdings: 500, tasks: 500 }).load();
		expect(result.holdingsError).toBe(true);
		expect(result.allocations).toEqual([]);
		expect(result.tasks).toEqual([]);
	});
});

describe('the book client-record loader (write-enabled, book:write)', () => {
	const WRITE = ['book:read', 'book:write'];

	it('also loads instructions, with ?tenant= on that read too', async () => {
		const { calls, load } = run({}, WRITE);
		const result = await load();
		expect(calls).toHaveLength(5);
		for (const u of calls) expect(u.searchParams.get('tenant')).toBe(TENANT.uid);
		expect(result.canWrite).toBe(true);
		expect(result.instructions).toEqual(INSTRUCTIONS);
	});

	it('derives funding on the gate’s basis: raw provider legs, net of pending claims', async () => {
		const result = await run({}, WRITE).load();
		// Account 11 holds £100 cash / £300 invested; the pending withdrawal claims £40 of the
		// cash leg (the settled one claims nothing). NOT the AUM-scaled figures the ring shows.
		expect(result.funding).toEqual([
			{
				accountId: 11,
				cash: 100,
				invested: 300,
				asOf: '2026-07-30',
				committedCash: 40,
				committedInvested: 0,
				availableCash: 60,
				availableInvested: 300
			}
		]);
	});

	it('degrades gracefully when an older API 403s the write-side reads', async () => {
		// The sibling book-enablement may not have landed: the record must still render whole,
		// with no instruction history and gross funding legs — not an error page.
		const result = await run({ instructions: 403 }, WRITE).load();
		expect(result.instructions).toEqual([]);
		expect(result.funding).toEqual([
			expect.objectContaining({ accountId: 11, availableCash: 100, committedCash: 0 })
		]);
	});
});
