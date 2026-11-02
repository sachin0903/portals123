/** The client record's server actions — the money-movement control in particular.
 *
 *  `?/createInstruction` carries the highest-severity control on this screen: a cash withdrawal must
 *  be checked against the account's CASH, not its total value. The API enforces it too (see
 *  apps/api/tests/test_instructions.py — that is the layer that cannot be bypassed); these tests
 *  pin the portal's own defence in depth, and the refusals that must happen BEFORE any POST is made
 *  at all: an inactive client, a malformed or past due date, and a holdings read that failed.
 *
 *  The PURE money-classification helpers (basisByAccount / committedByAccount / accountFunding)
 *  moved to @graphene/ui book/clients with the shared record component; their tests live there
 *  (pkgs/ui .../book/clients/funding.test.ts). Everything here drives the exported action
 *  directly with a fake `fetch`, so a test can assert not
 *  just the message but that no instruction was posted.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { actions, load } from './+page.server';
import type { Instruction } from '$lib/api';

const ACCOUNT = 7;
const CASH = 2_000;
const INVESTED = 48_000;

/** A pending instruction on ACCOUNT unless told otherwise. */
const pending = (over: Partial<Instruction> = {}): Instruction =>
	({
		uid: 'ins_x',
		kind: 'withdrawal',
		amount: CASH,
		status: 'pending',
		due_date: null,
		note: null,
		created_at: '2026-07-01T00:00:00Z',
		account_id: ACCOUNT,
		account_name: 'ISA',
		model_name: null,
		...over
	}) as Instruction;

/** One account's provider position: £2,000 cash (a money-market fund whose asset_type is the
 *  lake's coarse "Stock"), £48,000 invested. The account's own `value` is £100,000. */
const POSITION = [
	{
		account_id: ACCOUNT,
		asset_name: 'BlackRock ICS Sterling Liquidity Fund',
		asset_type: 'Stock',
		value: CASH,
		as_of: '2026-06-30'
	},
	{
		account_id: ACCOUNT,
		asset_name: 'Vanguard FTSE Global All Cap',
		asset_type: 'Stock',
		value: INVESTED,
		as_of: '2026-07-01'
	}
];

// The record's actions guard per family (docs/api.md "six splits"): client edits on
// contacts:write, money movement on instructions:write. The happy-path fixture holds both.
const locals = {
	user: { id: 'u_1' },
	accessToken: 'tok',
	permissions: ['contacts:write', 'instructions:write'],
	role: 'adviser'
} as unknown as App.Locals;

/** A principal holding only the given slugs — the narrowed-role postures. */
const localsWith = (...permissions: string[]) =>
	({
		user: { id: 'u_1' },
		accessToken: 'tok',
		permissions,
		role: 'adviser'
	}) as unknown as App.Locals;

type Call = { url: string; method: string; body: unknown };

/** A fake API: the contact re-check, the holdings read and the instruction POST, each overridable
 *  per test. Records every call so a test can prove what was (and wasn't) sent. */
function api(
	opts: {
		enabled?: boolean;
		contactStatus?: number;
		holdings?: unknown;
		holdingsStatus?: number;
		/** What the client's instruction list already holds (pending rows are claims on the money). */
		pending?: Instruction[];
		pendingStatus?: number;
		instructionStatus?: number;
	} = {}
) {
	const calls: Call[] = [];
	const json = (body: unknown, status = 200) =>
		new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
	const fetch = (async (url: string | URL, init?: RequestInit) => {
		const u = String(url);
		const method = init?.method ?? 'GET';
		calls.push({ url: u, method, body: init?.body });
		// The uid → id resolve every write action makes first, and the record re-read (which is
		// `by-uid` too — the endpoint answers with the whole record). `contactStatus` applies to
		// both, because they are the same call.
		if (u.includes('/notes') && method === 'POST') {
			const body = JSON.parse(String(init?.body ?? '{}')) as { body?: string };
			return json(
				{
					id: 9,
					uid: 'nte_1',
					body: body.body ?? '',
					created_at: '2026-09-20T12:00:00Z',
					author: 'Ada'
				},
				201
			);
		}
		if (u.includes('/notes') && method === 'DELETE') {
			return json({ ok: true });
		}
		if (u.includes('/crm/contacts/')) {
			if (opts.contactStatus) return json({ detail: 'nope' }, opts.contactStatus);
			return json({ contact: { id: 42, enabled: opts.enabled ?? true }, accounts: [] });
		}
		// Anything else on the contact — the record re-read and the PATCH/PUT write paths. The
		// instruction branches below own `/instructions`, so this must not swallow them.
		if (
			(u.includes('/crm/contacts/') || u.includes('/firm/contacts/')) &&
			!u.includes('/instructions')
		) {
			if (opts.contactStatus) return json({ detail: 'nope' }, opts.contactStatus);
			return json({ contact: { id: 42, enabled: opts.enabled ?? true }, accounts: [] });
		}
		if (u.includes('/crm/holdings')) {
			if (opts.holdingsStatus) return json({ detail: 'nope' }, opts.holdingsStatus);
			return json(opts.holdings ?? POSITION);
		}
		if (u.includes('/instructions') && method === 'GET') {
			if (opts.pendingStatus) return json({ detail: 'nope' }, opts.pendingStatus);
			return json(opts.pending ?? []);
		}
		if (u.includes('/instructions')) {
			if (opts.instructionStatus) return json({ detail: 'API said no' }, opts.instructionStatus);
			return json({ uid: 'ins_1' }, 201);
		}
		throw new Error(`unexpected fetch: ${u}`);
	}) as unknown as typeof globalThis.fetch;
	return {
		// The uid → id resolve every write action makes first is plumbing, not the act under test:
		// it is excluded here so the positional assertions below still read "the first thing this
		// action did to the API". `rawCalls` keeps it for anything that wants to see it.
		get calls() {
			return calls.filter((c) => !c.url.includes('/by-uid/'));
		},
		rawCalls: calls,
		fetch,
		posted: () => calls.filter((c) => c.method === 'POST' && c.url.includes('/instructions'))
	};
}

function submit(fields: Record<string, string | string[]>) {
	const body = new FormData();
	for (const [k, value] of Object.entries(fields)) {
		for (const v of Array.isArray(value) ? value : [value]) body.append(k, v);
	}
	return new Request('http://portal.test/clients/con_9b2e', { method: 'POST', body });
}

const createInstruction = async (
	fields: Record<string, string>,
	opts: Parameters<typeof api>[0] = {}
) => {
	const a = api(opts);
	// The action signature is SvelteKit's RequestEvent; only these four members are used.
	const result = (await actions.createInstruction!({
		request: submit(fields),
		locals,
		params: { uid: 'con_9b2e' },
		fetch: a.fetch
	} as never)) as { status?: number; data?: { error?: string } };
	return { result, ...a };
};

const TOMORROW = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);

describe('?/createInstruction — the withdrawal control', () => {
	it('refuses a cash withdrawal larger than the cash leg, and posts nothing', async () => {
		// The account is worth £100,000 and holds £2,000 of cash. This used to be accepted because
		// the check compared the amount against the account's TOTAL value.
		const { result, posted } = await createInstruction({
			kind: 'withdrawal',
			account_id: String(ACCOUNT),
			amount: '10000'
		});
		expect(result.status).toBe(400);
		expect(result.data?.error).toContain('£2,000.00');
		expect(result.data?.error).toContain('cash');
		expect(result.data?.error).toContain('2026-07-01'); // states how old the position is
		expect(posted()).toHaveLength(0);
	});

	it('allows a withdrawal within the cash leg', async () => {
		const { result, posted } = await createInstruction({
			kind: 'withdrawal',
			account_id: String(ACCOUNT),
			amount: String(CASH)
		});
		expect(result).toMatchObject({ action: 'createInstruction', ok: true });
		expect(posted()).toHaveLength(1);
	});

	it('checks a model withdrawal against what is invested, not the cash or the total', async () => {
		const over = await createInstruction({
			kind: 'model_withdrawal',
			account_id: String(ACCOUNT),
			amount: '60000'
		});
		expect(over.result.status).toBe(400);
		expect(over.result.data?.error).toContain('invested in the model');
		expect(over.posted()).toHaveLength(0);

		const ok = await createInstruction({
			kind: 'model_withdrawal',
			account_id: String(ACCOUNT),
			amount: String(INVESTED)
		});
		expect(ok.posted()).toHaveLength(1);
	});

	it('refuses when the account has no provider position at all', async () => {
		// Fail-closed: "we can't tell" must not mean "go ahead" for a cash-out.
		const { result, posted } = await createInstruction(
			{ kind: 'withdrawal', account_id: String(ACCOUNT), amount: '1' },
			{ holdings: [] }
		);
		expect(result.status).toBe(400);
		expect(posted()).toHaveLength(0);
	});

	it('refuses when the holdings read fails', async () => {
		const { result, posted } = await createInstruction(
			{ kind: 'withdrawal', account_id: String(ACCOUNT), amount: '1' },
			{ holdingsStatus: 500 }
		);
		expect(result.status).toBe(502);
		expect(result.data?.error).toContain('wasn’t raised');
		expect(posted()).toHaveLength(0);
	});

	it('counts what pending instructions have already claimed, so the leg can’t be spent twice', async () => {
		// The surviving exploit after the per-request check: the £2,000 cash leg was withdrawn again
		// and again because nothing looked at the withdrawals already pending.
		const { result, posted } = await createInstruction(
			{ kind: 'withdrawal', account_id: String(ACCOUNT), amount: String(CASH) },
			{ pending: [pending({ amount: CASH })] }
		);
		expect(result.status).toBe(400);
		expect(result.data?.error).toContain('already committed to pending instructions');
		expect(result.data?.error).toContain('£0.00'); // what is actually available
		expect(posted()).toHaveLength(0);
	});

	it('treats a pending model top-up as a claim on the same cash', async () => {
		// Otherwise the cash gate is defeated by swapping kinds: top up the whole cash leg into the
		// model, then withdraw the whole cash leg.
		const { result, posted } = await createInstruction(
			{ kind: 'withdrawal', account_id: String(ACCOUNT), amount: '1' },
			{ pending: [pending({ kind: 'model_topup', amount: CASH })] }
		);
		expect(result.status).toBe(400);
		expect(posted()).toHaveLength(0);
	});

	it('treats a pending ad hoc fee as a claim on the same cash', async () => {
		const { result, posted } = await createInstruction(
			{ kind: 'withdrawal', account_id: String(ACCOUNT), amount: '1' },
			{ pending: [pending({ kind: 'adhoc_fee', amount: CASH })] }
		);
		expect(result.status).toBe(400);
		expect(posted()).toHaveLength(0);
	});

	it('checks a model top-up against the cash it would have to be funded from', async () => {
		const over = await createInstruction({
			kind: 'model_topup',
			account_id: String(ACCOUNT),
			amount: String(CASH + 1)
		});
		expect(over.result.status).toBe(400);
		expect(over.result.data?.error).toContain('cash the account already holds');
		expect(over.posted()).toHaveLength(0);

		const ok = await createInstruction({
			kind: 'model_topup',
			account_id: String(ACCOUNT),
			amount: String(CASH)
		});
		expect(ok.posted()).toHaveLength(1);
	});

	it('ignores claims that belong to another account, or that are no longer pending', async () => {
		const { result, posted } = await createInstruction(
			{ kind: 'withdrawal', account_id: String(ACCOUNT), amount: String(CASH) },
			{
				pending: [
					pending({ amount: CASH, account_id: 99 }),
					pending({ amount: CASH, status: 'cancelled' })
				]
			}
		);
		expect(result).toMatchObject({ ok: true });
		expect(posted()).toHaveLength(1);
	});

	it('refuses when the pending instructions can’t be read', async () => {
		// Half the basis is missing: we know the leg but not what is already promised off it.
		const { result, posted } = await createInstruction(
			{ kind: 'withdrawal', account_id: String(ACCOUNT), amount: '1' },
			{ pendingStatus: 500 }
		);
		expect(result.status).toBe(502);
		expect(posted()).toHaveLength(0);
	});

	it('leaves money coming IN unconstrained by what is held', async () => {
		const { result, posted } = await createInstruction({
			kind: 'expected_in',
			account_id: String(ACCOUNT),
			amount: '250000',
			due_date: TOMORROW
		});
		expect(result).toMatchObject({ ok: true });
		expect(posted()).toHaveLength(1);
	});
});

describe('the six-splits action guards — contacts:write and instructions:write are different pens', () => {
	/** Drive an action with a narrowed principal and a fetch that must never be reached. */
	const drive = (name: keyof typeof actions, who: App.Locals, fields: Record<string, string>) =>
		actions[name]!({
			request: submit(fields),
			locals: who,
			params: { uid: 'con_9b2e' },
			fetch: (() => {
				throw new Error('the guard let an unauthorised action reach the API');
			}) as never
		} as never);

	it('money actions refuse contacts:write alone — yesterday’s key no longer turns them', async () => {
		const clientEditor = localsWith('contacts:write');
		for (const [name, fields] of [
			['createInstruction', { kind: 'withdrawal', account_id: '7', amount: '1' }],
			['cancelInstruction', { uid: 'ins_1' }],
			['updateBankAccount', { bank_name: 'T', bank_sort_code: '20-00-00' }],
			['setAccountModel', { account_uid: 'acc_7', model_id: '3' }]
		] as const) {
			await expect(drive(name, clientEditor, fields), name).rejects.toMatchObject({
				status: 303,
				location: '/clients/con_9b2e'
			});
		}
	});

	it('addNote returns the created note so the rail can show it without a refresh', async () => {
		const a = api();
		const result = (await actions.addNote!({
			request: submit({ body: 'Seen today.' }),
			locals: localsWith('contacts:write'),
			params: { uid: 'con_9b2e' },
			fetch: a.fetch
		} as never)) as Record<string, unknown>;
		expect(result).toMatchObject({
			action: 'addNote',
			ok: true,
			note: { uid: 'nte_1', body: 'Seen today.', author: 'Ada' }
		});
	});

	it('client-edit actions refuse instructions:write alone — money is not the client record', async () => {
		const moneyMover = localsWith('instructions:write');
		for (const [name, fields] of [
			['updateClient', { type: 'individual', first_name: 'T' }],
			['closeClient', {}],
			['restoreClient', {}],
			['invitePortal', {}],
			['addNote', { body: 'x' }]
		] as const) {
			await expect(drive(name, moneyMover, fields), name).rejects.toMatchObject({
				status: 303,
				location: '/clients/con_9b2e'
			});
		}
	});

	it('instructions:write alone raises an instruction (no contacts:write needed)', async () => {
		const a = api();
		const result = (await actions.createInstruction!({
			request: submit({ kind: 'withdrawal', account_id: String(ACCOUNT), amount: '1' }),
			locals: localsWith('instructions:write'),
			params: { uid: 'con_9b2e' },
			fetch: a.fetch
		} as never)) as Record<string, unknown>;
		expect(result).toMatchObject({ action: 'createInstruction', ok: true });
		expect(a.posted()).toHaveLength(1);
	});

	it('syncToMorganLloyd refuses contacts:write alone — yesterday’s key no longer turns it', async () => {
		await expect(
			drive('syncToMorganLloyd', localsWith('contacts:write'), {})
		).rejects.toMatchObject({
			status: 303,
			location: '/clients/con_9b2e'
		});
	});
});

describe('?/createInstruction — the other refusals', () => {
	it('refuses any instruction against an inactive client, before reading holdings', async () => {
		const { result, calls, posted } = await createInstruction(
			{ kind: 'withdrawal', account_id: String(ACCOUNT), amount: '1' },
			{ enabled: false }
		);
		expect(result.status).toBe(409);
		expect(result.data?.error).toContain('inactive');
		expect(calls.some((c) => c.url.includes('/crm/holdings'))).toBe(false);
		expect(posted()).toHaveLength(0);
	});

	it('refuses when the client re-check itself fails', async () => {
		const { result, posted } = await createInstruction(
			{ kind: 'expected_in', account_id: String(ACCOUNT), amount: '100' },
			{ contactStatus: 500 }
		);
		expect(result.status).toBeGreaterThanOrEqual(500);
		expect(result.data?.error).toContain('nothing was recorded');
		expect(posted()).toHaveLength(0);
	});

	it('rejects a malformed due date instead of letting the API 500 on the SQL cast', async () => {
		for (const due_date of ['tomorrow', '31/01/2027', '2027-1-5']) {
			const { result, posted } = await createInstruction({
				kind: 'expected_in',
				account_id: String(ACCOUNT),
				amount: '100',
				due_date
			});
			expect(result.status, due_date).toBe(400);
			expect(result.data?.error).toContain('valid date');
			expect(posted()).toHaveLength(0);
		}
	});

	it('lets a payment in be backdated — the API holds the floor — but not a withdrawal', async () => {
		// The client paid last week and the record is being made now. The past was refused here
		// for every kind; now only money the custodian has yet to move is held to the future.
		const lastWeek = new Date(Date.now() - 7 * 86_400_000).toISOString().slice(0, 10);
		const backdated = await createInstruction({
			kind: 'expected_in',
			account_id: String(ACCOUNT),
			amount: '100',
			due_date: lastWeek
		});
		expect(backdated.result).toMatchObject({ ok: true });
		expect(backdated.posted()).toHaveLength(1);
		expect(JSON.parse(String(backdated.posted()[0]?.body)).due_date).toBe(lastWeek);

		const withdrawal = await createInstruction({
			kind: 'withdrawal',
			account_id: String(ACCOUNT),
			amount: '100',
			due_date: '2020-01-01'
		});
		expect(withdrawal.result.status).toBe(400);
		expect(withdrawal.result.data?.error).toContain('past');
		expect(withdrawal.posted()).toHaveLength(0);
	});

	it('rejects a non-positive or non-numeric amount', async () => {
		for (const amount of ['0', '-100', 'lots']) {
			const { result, posted } = await createInstruction({
				kind: 'withdrawal',
				account_id: String(ACCOUNT),
				amount
			});
			expect(result.status, amount).toBe(400);
			expect(posted()).toHaveLength(0);
		}
	});

	it('surfaces the API’s own refusal rather than claiming success', async () => {
		const { result } = await createInstruction(
			{ kind: 'expected_in', account_id: String(ACCOUNT), amount: '100' },
			{ instructionStatus: 400 }
		);
		expect(result.status).toBe(400);
		expect(result.data?.error).toBe('API said no');
	});
});

/**
 * The uid → id resolve every write action makes before it can call an id-typed endpoint.
 *
 * The local harnesses below answer one canned response to every URL, which would make the resolve
 * indistinguishable from the act under test — and would let an injected failure status break the
 * resolve instead of the write. This intercepts it: the resolve is answered with the record, is
 * NOT recorded in `calls`, and never sees the injected status.
 */
const RESOLVED = () =>
	new Response(JSON.stringify({ contact: { id: 42 } }), {
		status: 200,
		headers: { 'content-type': 'application/json' }
	});
const isResolve = (url: string | URL): boolean => String(url).includes('/by-uid/');

describe('?/updateClient', () => {
	const update = async (
		fields: Record<string, string | string[]>,
		saved: Record<string, unknown>
	) => {
		const calls: Call[] = [];
		const fetch = (async (url: string | URL, init?: RequestInit) => {
			if (isResolve(url)) return RESOLVED();
			calls.push({ url: String(url), method: init?.method ?? 'GET', body: init?.body });
			return new Response(JSON.stringify(saved), {
				status: 200,
				headers: { 'content-type': 'application/json' }
			});
		}) as unknown as typeof globalThis.fetch;
		const result = (await actions.updateClient!({
			request: submit(fields),
			locals,
			params: { uid: 'con_9b2e' },
			fetch
		} as never)) as { status?: number; data?: { error?: string }; warning?: string };
		const body = calls[0] ? JSON.parse(String(calls[0].body)) : {};
		return { result, body };
	};

	it("leaves a person's name to the database, which builds it from the parts", async () => {
		// The database owns a person's `name`: first, middle and surname, whoever writes the parts
		// (0239). A sent name would be replaced anyway, and a null would blank the name every
		// downstream consumer falls back to.
		const { body } = await update(
			{ type: 'individual', first_name: 'Theo', surname: 'Caldwell' },
			{ name: 'Theo Caldwell' }
		);
		expect(body).not.toHaveProperty('name');
		expect(body.first_name).toBe('Theo');
	});

	it('keeps an organisation on its own name field', async () => {
		const { body } = await update(
			{ type: 'organisation', name: 'Caldwell Holdings Ltd', first_name: 'ignored' },
			{ name: 'Caldwell Holdings Ltd' }
		);
		expect(body.name).toBe('Caldwell Holdings Ltd');
		expect(body.first_name).toBeNull();
	});

	it('rejects a malformed email before sending anything', async () => {
		const { result } = await update({ type: 'individual', email: 'not-an-email' }, {});
		expect(result.status).toBe(400);
		expect(result.data?.error).toContain('email');
	});

	it('warns instead of claiming success when a requested clear did not happen', async () => {
		// The API builds its SET clause from non-null fields only, so a clear can be silently
		// dropped. The toast must not say "Client updated" over a no-op.
		const { result } = await update(
			{ type: 'individual', first_name: 'Theo', surname: 'Caldwell', mobile: '' },
			{ name: 'Theo Caldwell', mobile: '07700 900000' }
		);
		expect(result.warning).toContain('mobile');
	});

	it('reports plain success when everything asked for was applied', async () => {
		const { result } = await update(
			{ type: 'individual', first_name: 'Theo', surname: 'Caldwell', mobile: '' },
			{ name: 'Theo Caldwell', mobile: null }
		);
		expect(result.warning).toBeUndefined();
		expect(result).toMatchObject({ ok: true });
	});

	it('sends the other number as a string, and a blank as null', async () => {
		const kept = await update({ type: 'individual', phone: '0117 900 0001' }, {});
		expect(kept.body.phone).toBe('0117 900 0001');

		const cleared = await update({ type: 'individual', phone: '' }, { phone: null });
		expect(cleared.body.phone).toBeNull();
		expect(cleared.result.warning).toBeUndefined();
	});

	it('leaves the other number alone when the form did not carry it', async () => {
		const { body } = await update({ type: 'individual', mobile: '07700 900000' }, {});
		expect(body).not.toHaveProperty('phone');
		expect(body.mobile).toBe('07700 900000');
	});

	it('records the US-person answer on its own', async () => {
		const isUs = await update({ type: 'individual', us_person: 'Yes' }, {});
		expect(isUs.body.us_person).toBe(true);
		expect(isUs.body).not.toHaveProperty('fatca_status');
		expect(isUs.body).not.toHaveProperty('fatca_certified_at');

		const notUs = await update({ type: 'individual', us_person: 'No' }, {});
		expect(notUs.body.us_person).toBe(false);
		expect(notUs.body).not.toHaveProperty('fatca_status');

		const unanswered = await update({ type: 'individual', us_person: '' }, {});
		expect(unanswered.body).not.toHaveProperty('us_person');
	});

	it('records the W-9 or W-8 on its own and stamps the date', async () => {
		const today = new Date().toISOString().slice(0, 10);
		const w8 = await update({ type: 'individual', fatca_status: 'w8' }, {});
		expect(w8.body.fatca_status).toBe('w8');
		expect(w8.body.fatca_certified_at).toBe(today);
		expect(w8.body).not.toHaveProperty('us_person');

		const cleared = await update({ type: 'individual', fatca_status: '' }, {});
		expect(cleared.body.fatca_status).toBeNull();
		expect(cleared.body.fatca_certified_at).toBeNull();
	});

	it('leaves an unchanged W-9 or W-8 and its date alone', async () => {
		const same = await update(
			{ type: 'individual', fatca_status: 'w8', fatca_status_was: 'w8' },
			{}
		);
		expect(same.body).not.toHaveProperty('fatca_status');
		expect(same.body).not.toHaveProperty('fatca_certified_at');

		const changed = await update(
			{ type: 'individual', fatca_status: 'w9', fatca_status_was: 'w8' },
			{}
		);
		expect(changed.body.fatca_status).toBe('w9');

		// An unchanged W-8 is still checked against a new US-person answer.
		const nowUs = await update(
			{ type: 'individual', us_person: 'Yes', fatca_status: 'w8', fatca_status_was: 'w8' },
			{}
		);
		expect(nowUs.result.status).toBe(400);
	});

	it('accepts a coherent pair and refuses an incoherent one', async () => {
		const ok = await update({ type: 'individual', us_person: 'Yes', fatca_status: 'w9' }, {});
		expect(ok.body.us_person).toBe(true);
		expect(ok.body.fatca_status).toBe('w9');

		const usW8 = await update({ type: 'individual', us_person: 'Yes', fatca_status: 'w8' }, {});
		expect(usW8.result.status).toBe(400);
		expect(usW8.result.data?.error).toBe('A US person has a W-9, not a W-8.');
		expect(usW8.body).toEqual({});

		const nonUsW9 = await update({ type: 'individual', us_person: 'No', fatca_status: 'w9' }, {});
		expect(nonUsW9.result.status).toBe(400);
		expect(nonUsW9.result.data?.error).toBe('A W-9 is only for a US person.');
	});

	it('checks a blank US-person answer against the one already on the record', async () => {
		const usW8 = await update(
			{ type: 'individual', us_person: '', us_person_was: 'Yes', fatca_status: 'w8' },
			{}
		);
		expect(usW8.result.status).toBe(400);
		expect(usW8.result.data?.error).toBe('A US person has a W-9, not a W-8.');
		expect(usW8.body).toEqual({});

		const nonUsW9 = await update(
			{ type: 'individual', us_person: '', us_person_was: 'No', fatca_status: 'w9' },
			{}
		);
		expect(nonUsW9.result.status).toBe(400);
		expect(nonUsW9.result.data?.error).toBe('A W-9 is only for a US person.');

		const coherent = await update(
			{ type: 'individual', us_person: '', us_person_was: 'Yes', fatca_status: 'w9' },
			{}
		);
		expect(coherent.body.fatca_status).toBe('w9');
		expect(coherent.body).not.toHaveProperty('us_person');
	});

	it('posts source of wealth, and the Other words only when they are on the form', async () => {
		const named = await update(
			{ type: 'individual', source_of_wealth: 'Salary' },
			{ source_of_wealth: 'Salary' }
		);
		expect(named.body.source_of_wealth).toBe('Salary');
		expect(named.body).not.toHaveProperty('source_of_wealth_detail');

		const other = await update(
			{
				type: 'individual',
				source_of_wealth: 'Other',
				source_of_wealth_detail: 'Sale of a vintage car'
			},
			{ source_of_wealth: 'Other', source_of_wealth_detail: 'Sale of a vintage car' }
		);
		expect(other.body.source_of_wealth).toBe('Other');
		expect(other.body.source_of_wealth_detail).toBe('Sale of a vintage car');
	});

	it('does not post source of wealth for an organisation', async () => {
		// Those person-facts are individuals only. A stray field on an organisation form must
		// not reach the column — the same `!isOrg` gate the card uses.
		const { body } = await update(
			{ type: 'organisation', name: 'Caldwell Trust', source_of_wealth: 'Gift' },
			{ name: 'Caldwell Trust' }
		);
		expect(body).not.toHaveProperty('source_of_wealth');
		expect(body).not.toHaveProperty('source_of_wealth_detail');
	});

	it('keeps every selected tax residency', async () => {
		const picked = await update(
			{ type: 'individual', tax_residency: ['GB', 'JE'] },
			{ tax_residency: ['GB', 'JE'] }
		);
		expect(picked.body.tax_residency).toEqual(['GB', 'JE']);

		const cleared = await update({ type: 'individual', tax_residency: '' }, { tax_residency: [] });
		expect(cleared.body.tax_residency).toEqual([]);
	});

	it('posts retirement age as an integer, stripping anything that is not a digit', async () => {
		const { body } = await update(
			{ type: 'individual', retirement_age: '65 years' },
			{ retirement_age: 65 }
		);
		expect(body.retirement_age).toBe(65);
	});
});

describe('?/updateClient — the signed W-9 or W-8', () => {
	const pdf = (name = 'w9-signed.pdf') => new File(['%PDF-1.7'], name, { type: 'application/pdf' });
	/** The browser's entry for a file input with nothing chosen. */
	const nothing = () => new File([], '', { type: 'application/octet-stream' });

	const save = async (
		fields: Record<string, string | File>,
		opts: { patchStatus?: number; uploadStatus?: number } = {}
	) => {
		const calls: { url: string; method: string; headers: Headers; body: unknown }[] = [];
		const fetch = (async (url: string | URL, init?: RequestInit) => {
			if (isResolve(url)) return RESOLVED();
			const u = String(url);
			calls.push({
				url: u,
				method: init?.method ?? 'GET',
				headers: new Headers(init?.headers),
				body: init?.body
			});
			if (u.endsWith('/fatca-document')) {
				return opts.uploadStatus
					? Response.json(
							{ detail: 'Only a W-9 or a W-8 takes a signed form.' },
							{ status: opts.uploadStatus }
						)
					: Response.json({ fatca_document: { uid: 'doc_w9', name: 'w9-signed.pdf' } });
			}
			return opts.patchStatus
				? Response.json({ detail: 'API said no' }, { status: opts.patchStatus })
				: Response.json({});
		}) as unknown as typeof globalThis.fetch;
		const body = new FormData();
		for (const [k, v] of Object.entries(fields)) body.append(k, v);
		const result = (await actions.updateClient!({
			request: new Request('http://portal.test/clients/con_9b2e', { method: 'POST', body }),
			locals,
			params: { uid: 'con_9b2e' },
			fetch
		} as never)) as { status?: number; ok?: boolean; data?: { error?: string; field?: string } };
		return { result, calls, uploads: calls.filter((c) => c.url.endsWith('/fatca-document')) };
	};

	it('uploads the file after the PATCH has saved the answer', async () => {
		const { result, calls, uploads } = await save({
			type: 'individual',
			us_person: 'Yes',
			fatca_status: 'w9',
			fatca_status_was: '',
			fatca_document: pdf()
		});
		expect(result).toMatchObject({ ok: true });
		expect(calls.map((c) => `${c.method} ${new URL(c.url).pathname}`)).toEqual([
			'PATCH /firm/contacts/con_9b2e',
			'POST /crm/contacts/con_9b2e/fatca-document'
		]);
		const [upload] = uploads;
		expect(upload.headers.get('authorization')).toBe('Bearer tok');
		// fetch sets the multipart boundary itself; a hand-set content type would lose it.
		expect(upload.headers.has('content-type')).toBe(false);
		const file = (upload.body as FormData).get('file');
		expect(file).toBeInstanceOf(File);
		expect((file as File).name).toBe('w9-signed.pdf');
		expect(await (file as File).text()).toBe('%PDF-1.7');
	});

	it('uploads for an unchanged W-8 too — the file is added to the answer on record', async () => {
		const { uploads } = await save({
			type: 'individual',
			fatca_status: 'w8',
			fatca_status_was: 'w8',
			fatca_document: pdf('w8.pdf')
		});
		expect(uploads).toHaveLength(1);
	});

	it('uploads nothing when the PATCH fails', async () => {
		const { result, uploads } = await save(
			{ type: 'individual', fatca_status: 'w9', fatca_status_was: '', fatca_document: pdf() },
			{ patchStatus: 422 }
		);
		expect(result.status).toBe(422);
		expect(result.data?.error).toBe('API said no');
		expect(uploads).toHaveLength(0);
	});

	it('uploads nothing when the save clears the answer or leaves another code', async () => {
		for (const fatca_status of ['', 'w8_cure']) {
			const { result, calls, uploads } = await save({
				type: 'individual',
				fatca_status,
				fatca_status_was: 'w9',
				fatca_document: pdf()
			});
			expect(result, fatca_status).toMatchObject({ ok: true });
			expect(calls, fatca_status).toHaveLength(1);
			expect(uploads, fatca_status).toHaveLength(0);
		}
	});

	it('uploads nothing when no file was chosen', async () => {
		const { uploads } = await save({
			type: 'individual',
			fatca_status: 'w9',
			fatca_status_was: 'w9',
			fatca_document: nothing()
		});
		expect(uploads).toHaveLength(0);
	});

	it('sends nothing at all when the answer is refused before the PATCH', async () => {
		const { result, calls } = await save({
			type: 'individual',
			us_person: 'No',
			fatca_status: 'w9',
			fatca_document: pdf()
		});
		expect(result.status).toBe(400);
		expect(calls).toHaveLength(0);
	});

	it('says the answer is saved when only the upload fails', async () => {
		const { result, calls } = await save(
			{ type: 'individual', fatca_status: 'w9', fatca_status_was: '', fatca_document: pdf() },
			{ uploadStatus: 409 }
		);
		expect(calls.map((c) => c.method)).toEqual(['PATCH', 'POST']);
		expect(result.status).toBe(409);
		expect(result.data?.field).toBe('fatca_document');
		expect(result.data?.error).toBe(
			'The answer is saved, but the form did not upload: Only a W-9 or a W-8 takes a signed form.'
		);
	});
});

describe('?/closeClient', () => {
	const close = async (status = 200) => {
		const calls: Call[] = [];
		const fetch = (async (url: string | URL, init?: RequestInit) => {
			if (isResolve(url)) return RESOLVED();
			calls.push({ url: String(url), method: init?.method ?? 'GET', body: init?.body });
			return new Response(
				JSON.stringify(
					status === 200
						? { id: 42, deleted_at: '2026-07-28T00:00:00Z' }
						: { detail: 'API said no' }
				),
				{ status, headers: { 'content-type': 'application/json' } }
			);
		}) as unknown as typeof globalThis.fetch;
		const result = (await actions.closeClient!({
			request: submit({}),
			locals,
			params: { uid: 'con_9b2e' },
			fetch
		} as never)) as { status?: number; data?: { error?: string } };
		return { result, calls };
	};

	it('soft-deletes through DELETE /firm/contacts/{contact_uid}', async () => {
		const { result, calls } = await close();
		expect(calls[0].method).toBe('DELETE');
		expect(calls[0].url).toContain('/firm/contacts/con_9b2e');
		expect(result).toMatchObject({ ok: true });
	});

	it('passes the API’s refusal through — a balance or money in flight is a reason, not a detail to hide', async () => {
		const { result } = await close(409);
		expect(result.status).toBe(409);
		expect(result.data?.error).toBe('API said no');
	});
});

describe('?/invitePortal', () => {
	it('posts to POST /firm/contacts/{uid}/portal-invite', async () => {
		const calls: Call[] = [];
		const fetch = (async (url: string | URL, init?: RequestInit) => {
			if (isResolve(url)) return RESOLVED();
			calls.push({ url: String(url), method: init?.method ?? 'GET', body: init?.body });
			return new Response(JSON.stringify({ invited: true }), {
				status: 200,
				headers: { 'content-type': 'application/json' }
			});
		}) as unknown as typeof globalThis.fetch;
		const result = (await actions.invitePortal!({
			request: submit({}),
			locals: localsWith('contacts:write', 'members:write'),
			params: { uid: 'con_9b2e' },
			fetch
		} as never)) as { ok?: boolean };
		expect(calls[0].method).toBe('POST');
		expect(calls[0].url).toContain('/firm/contacts/con_9b2e/portal-invite');
		expect(result).toMatchObject({ ok: true });
	});
});

describe('load — the old ?tab= deep links', () => {
	/** The record's sections are routes now, so `?tab=` has to be honoured rather than ignored: the
	 *  dashboard's action-items rail links to `/clients/{id}?tab=documents`, and a link that lands on
	 *  the overview instead of the documents it named is a silent regression. */
	const deepLink = (search: string) =>
		load({
			locals,
			params: { uid: 'con_9b2e' },
			url: new URL(`http://portal.test/clients/con_9b2e${search}`),
			// A deep link must redirect BEFORE it pays for the record: both of these throw if it doesn't.
			fetch: (() => {
				throw new Error('redirected too late — the load fetched first');
			}) as never,
			parent: async () => {
				throw new Error('redirected too late — the load awaited parent first');
			}
		} as never);

	it('sends every tab that became a route to that route', async () => {
		const cases = [
			['documents', '/clients/con_9b2e/documents'],
			// The ?tab=activity deep link lands on the client's own event trail, which is now
			// `logs` — renamed with the firm-wide surface. THIRD place this target is pinned
			// (with CLIENT_TAB_ROUTES and sections.test.ts); all three move together.
			['activity', '/clients/con_9b2e/activity'],
			['transactions', '/clients/con_9b2e/transactions'],
			['details', '/clients/con_9b2e/details'],
			// `people` is not a section: an old deep link lands on the record. Illustrations is a
			// section again, so that deep link goes to the illustrations route.
			['people', '/clients/con_9b2e'],
			['illustrations', '/clients/con_9b2e/illustrations']
		] as const;
		for (const [tab, location] of cases) {
			await expect(deepLink(`?tab=${tab}`), tab).rejects.toMatchObject({ status: 307, location });
		}
	});

	it('lands an unknown or empty tab on the record instead of erroring', async () => {
		await expect(deepLink('?tab=holdings')).rejects.toMatchObject({
			location: '/clients/con_9b2e'
		});
		await expect(deepLink('?tab=')).rejects.toMatchObject({ location: '/clients/con_9b2e' });
		await expect(deepLink('?tab=overview')).rejects.toMatchObject({
			location: '/clients/con_9b2e'
		});
	});

	it('renders — never redirects — for the tabs that folded into this page', async () => {
		// Accounts and Notes are bands on Overview. Details became its own route.
		for (const tab of ['accounts', 'notes', '']) {
			const search = tab ? `?tab=${tab}` : '';
			const result = await renders(search);
			expect(result, tab).toMatchObject({ allocations: [], cash: 0, instructions: [] });
		}
	});

	/** Run the load for real (no redirect expected), with a fake API that answers everything empty. */
	const renders = (search: string) => {
		const fetch = (async (url: string | URL) => {
			const u = String(url);
			if (u.includes('/crm/contacts/') && !u.includes('/shell') && !u.includes('/holdings')) {
				return new Response(
					JSON.stringify({ contact: { id: 42, enabled: true, uid: 'con_9b2e' }, accounts: [] }),
					{ status: 200, headers: { 'content-type': 'application/json' } }
				);
			}
			return new Response('[]', {
				status: 200,
				headers: { 'content-type': 'application/json' }
			});
		}) as unknown as typeof globalThis.fetch;
		return (async () => {
			const loaded = (await load({
				locals,
				params: { uid: 'con_9b2e' },
				url: new URL(`http://portal.test/clients/con_9b2e${search}`),
				fetch,
				parent: async () => ({
					detail: { contact: { id: 42, enabled: true }, accounts: [] },
					canManage: false,
					canReadInstructions: false,
					canIllustrate: false
				})
			} as never)) as { record: Promise<Record<string, unknown>> };
			return { ...loaded, ...(await loaded.record) };
		})();
	};
});

describe('load — first-paint fetches', () => {
	it('loads the fat record and the holdings summary, not pickers or unpaged holdings', async () => {
		const urls: string[] = [];
		const fetch = (async (url: string | URL) => {
			const u = String(url);
			urls.push(u);
			if (u.includes('/crm/contacts/') && !u.includes('/holdings')) {
				return new Response(
					JSON.stringify({ contact: { id: 42, enabled: true, uid: 'con_9b2e' }, accounts: [] }),
					{ status: 200, headers: { 'content-type': 'application/json' } }
				);
			}
			if (u.includes('/crm/holdings/summary')) {
				return new Response(JSON.stringify({ allocations: [], cash: 0, lake_total: 0 }), {
					status: 200,
					headers: { 'content-type': 'application/json' }
				});
			}
			return new Response('[]', {
				status: 200,
				headers: { 'content-type': 'application/json' }
			});
		}) as unknown as typeof globalThis.fetch;

		const loaded = (await load({
			locals: localsWith(
				'contacts:write',
				'instructions:write',
				'instructions:read',
				'illustrations:write'
			),
			params: { uid: 'con_9b2e' },
			url: new URL('http://portal.test/clients/con_9b2e'),
			fetch,
			parent: async () => ({
				detail: { contact: { id: 42, enabled: true }, accounts: [] },
				canManage: true,
				canReadInstructions: true,
				canIllustrate: true
			})
		} as never)) as { record: Promise<unknown> };
		await loaded.record;

		expect(urls.some((u) => /\/crm\/contacts\/con_9b2e$/.test(u))).toBe(true);
		expect(urls.some((u) => u.includes('/crm/holdings/summary'))).toBe(true);
		expect(urls.some((u) => u.includes('/crm/holdings?'))).toBe(false);
		expect(urls.some((u) => u.includes('/firm/members'))).toBe(false);
		expect(urls.some((u) => u.includes('/crm/models'))).toBe(false);
		expect(urls.some((u) => u.includes('/ref/countries'))).toBe(false);
		expect(urls.some((u) => u.includes('needs_action=true'))).toBe(true);
		expect(urls.some((u) => u.includes('/tasks'))).toBe(false);
		expect(urls.some((u) => u.includes('/journeys'))).toBe(false);
		expect(urls.some((u) => u.includes('/flows'))).toBe(false);
	});

	it('asks for the published flows only when the reader may start a journey', async () => {
		const urls: string[] = [];
		const fetch = (async (url: string | URL) => {
			const u = String(url);
			urls.push(u);
			if (u.includes('/flows')) {
				return new Response(JSON.stringify([{ code: 'fact_find', name: 'Fact find' }]), {
					status: 200,
					headers: { 'content-type': 'application/json' }
				});
			}
			if (u.includes('/crm/contacts/') && !u.includes('/holdings')) {
				return new Response(
					JSON.stringify({ contact: { id: 42, enabled: true, uid: 'con_9b2e' }, accounts: [] }),
					{ status: 200, headers: { 'content-type': 'application/json' } }
				);
			}
			return new Response('[]', {
				status: 200,
				headers: { 'content-type': 'application/json' }
			});
		}) as unknown as typeof globalThis.fetch;

		const loaded = (await load({
			locals: localsWith('cases:write'),
			params: { uid: 'con_9b2e' },
			url: new URL('http://portal.test/clients/con_9b2e'),
			fetch,
			parent: async () => ({
				detail: { contact: { id: 42, enabled: true, uid: 'con_9b2e' }, accounts: [] },
				canManage: false,
				canReadInstructions: false,
				canIllustrate: false,
				canReadJourneys: false
			})
		} as never)) as { record: Promise<{ newJourneys: unknown[] }> };
		const record = await loaded.record;

		expect(urls.some((u) => u.includes('/flows?status=published'))).toBe(true);
		expect(record.newJourneys).toEqual([
			{
				code: 'fact_find',
				name: 'Fact find',
				href: '/journeys/new?flow=fact_find&client=con_9b2e'
			}
		]);
	});

	it('asks for this client’s journeys when the reader can see them', async () => {
		const urls: string[] = [];
		const fetch = (async (url: string | URL) => {
			const u = String(url);
			urls.push(u);
			if (u.includes('/journeys')) {
				return new Response(JSON.stringify({ journeys: [] }), {
					status: 200,
					headers: { 'content-type': 'application/json' }
				});
			}
			if (u.includes('/crm/contacts/') && !u.includes('/holdings')) {
				return new Response(
					JSON.stringify({ contact: { id: 42, enabled: true, uid: 'con_9b2e' }, accounts: [] }),
					{ status: 200, headers: { 'content-type': 'application/json' } }
				);
			}
			return new Response('[]', {
				status: 200,
				headers: { 'content-type': 'application/json' }
			});
		}) as unknown as typeof globalThis.fetch;

		const loaded = (await load({
			locals,
			params: { uid: 'con_9b2e' },
			url: new URL('http://portal.test/clients/con_9b2e'),
			fetch,
			parent: async () => ({
				detail: { contact: { id: 42, enabled: true }, accounts: [] },
				canManage: false,
				canReadInstructions: false,
				canIllustrate: false,
				canReadJourneys: true
			})
		} as never)) as { record: Promise<{ journeys: unknown[] }> };
		const record = await loaded.record;

		expect(urls.some((u) => u.includes('/crm/contacts/con_9b2e/journeys'))).toBe(true);
		expect(record.journeys).toEqual([]);
	});
});

type PutResult = {
	status?: number;
	cleared?: boolean;
	data?: { error?: string; field?: string };
};

/** Drive one of the record's PUT actions with a fake API that records what was sent. */
async function putAction(
	name: 'updateBankAccount' | 'setAccountModel',
	fields: Record<string, string>,
	status = 200
): Promise<{ result: PutResult; calls: Call[] }> {
	const calls: Call[] = [];
	const fetch = (async (url: string | URL, init?: RequestInit) => {
		if (isResolve(url)) return RESOLVED();
		calls.push({ url: String(url), method: init?.method ?? 'GET', body: init?.body });
		return new Response(JSON.stringify(status === 200 ? { ok: true } : { detail: 'API said no' }), {
			status,
			headers: { 'content-type': 'application/json' }
		});
	}) as unknown as typeof globalThis.fetch;
	const result = (await actions[name]!({
		request: submit(fields),
		locals,
		params: { uid: 'con_9b2e' },
		fetch
	} as never)) as PutResult;
	return { result, calls };
}

describe('?/updateBankAccount', () => {
	const good = {
		bank_scheme: 'bacs',
		bank_name: 'T Caldwell',
		bank_sort_code: '20 00 00',
		bank_account_number: '1234567'
	};

	it('sends the canonical shape, not what was keyed', async () => {
		const { calls } = await putAction('updateBankAccount', good);
		expect(calls).toHaveLength(1);
		expect(calls[0].method).toBe('PUT');
		expect(calls[0].url).toContain('/crm/contacts/con_9b2e/bank-account');
		expect(JSON.parse(String(calls[0].body))).toEqual({
			name: 'T Caldwell',
			sort_code: '20-00-00',
			account_number: '01234567'
		});
	});

	it('refuses a malformed sort code without calling the API at all', async () => {
		const { result, calls } = await putAction('updateBankAccount', {
			...good,
			bank_sort_code: '2000'
		});
		expect(calls).toHaveLength(0);
		expect(result.status).toBe(400);
		expect(result.data?.field).toBe('sort_code');
		// The message must never carry the digits back.
		expect(result.data?.error).not.toContain('2000');
	});

	it('clears the details when every field is blank', async () => {
		const { result, calls } = await putAction('updateBankAccount', {
			bank_scheme: 'bacs',
			bank_name: '',
			bank_sort_code: '',
			bank_account_number: ''
		});
		expect(JSON.parse(String(calls[0].body))).toEqual({
			name: null,
			bank: null,
			sort_code: null,
			account_number: null,
			iban: null,
			bic: null,
			method: null
		});
		expect(result.cleared).toBe(true);
	});

	it('sends Faster Payment only when chosen — BACS is the default and is not sent', async () => {
		const { calls } = await putAction('updateBankAccount', {
			...good,
			bank_method: 'faster_payment'
		});
		expect(JSON.parse(String(calls[0].body))).toEqual({
			name: 'T Caldwell',
			sort_code: '20-00-00',
			account_number: '01234567',
			method: 'faster_payment'
		});
	});

	it('sends a compact IBAN when the international rail is posted', async () => {
		const { calls } = await putAction('updateBankAccount', {
			bank_scheme: 'iban',
			bank_name: 'T Caldwell',
			bank_iban: 'GB82 WEST 1234 5698 7654 32',
			bank_bic: 'barcgb22'
		});
		expect(JSON.parse(String(calls[0].body))).toEqual({
			name: 'T Caldwell',
			iban: 'GB82WEST12345698765432',
			bic: 'BARCGB22'
		});
	});

	it('passes the API refusal through rather than toasting a save it did not earn', async () => {
		const { result } = await putAction('updateBankAccount', good, 400);
		expect(result.status).toBe(400);
		expect(result.data?.error).toBe('API said no');
	});
});

describe('?/setAccountModel', () => {
	it('associates a model with the account', async () => {
		const { calls } = await putAction('setAccountModel', { account_uid: 'acc_7', model_id: '3' });
		expect(calls[0].method).toBe('PUT');
		// The account's UID in the path — `/crm/accounts/{account_uid}/model` resolves the serial
		// itself, and sending the serial 404s at the resolver.
		expect(calls[0].url).toContain('/crm/accounts/acc_7/model');
		expect(JSON.parse(String(calls[0].body))).toEqual({ model_id: 3 });
	});

	it('clears the mandate on an empty selection — advisory is a real choice, not a missing one', async () => {
		const { result, calls } = await putAction('setAccountModel', {
			account_uid: 'acc_7',
			model_id: ''
		});
		expect(JSON.parse(String(calls[0].body))).toEqual({ model_id: null });
		expect(result.cleared).toBe(true);
	});

	it('refuses without an account, and never guesses one', async () => {
		const { result, calls } = await putAction('setAccountModel', { model_id: '3' });
		expect(calls).toHaveLength(0);
		expect(result.status).toBe(400);
	});

	it('passes the 409 through when a pending model claim blocks the change', async () => {
		const { result } = await putAction(
			'setAccountModel',
			{ account_uid: 'acc_7', model_id: '' },
			409
		);
		expect(result.status).toBe(409);
		expect(result.data?.error).toBe('API said no');
	});
});

describe('?/assignAdviser — the client team', () => {
	const assign = async (fields: Record<string, string>, opts: Parameters<typeof api>[0] = {}) => {
		const a = api(opts);
		const result = (await actions.assignAdviser!({
			request: submit(fields),
			locals,
			params: { uid: 'con_9b2e' },
			fetch: a.fetch
		} as never)) as { status?: number; data?: { error?: string } };
		const posts = a.calls.filter((c) => c.method === 'POST' && c.url.includes('/advisers'));
		return { result, posts, calls: a.calls };
	};

	it('posts the role it was given, for each of the four', async () => {
		for (const role of ['adviser', 'paraplanner', 'administrator', 'support']) {
			const { result, posts } = await assign({ adviser_id: 'usr_1', role });
			expect(result, role).toMatchObject({ action: 'assignAdviser', ok: true });
			expect(posts, role).toHaveLength(1);
			expect(posts[0].url).toContain('/firm/contacts/con_9b2e/advisers');
			expect(JSON.parse(String(posts[0].body))).toEqual({ adviser_id: 'usr_1', role });
		}
	});

	it('means Adviser when no role is sent, as the API does', async () => {
		const blanks: Record<string, string>[] = [
			{ adviser_id: 'usr_1' },
			{ adviser_id: 'usr_1', role: '  ' }
		];
		for (const fields of blanks) {
			const { posts } = await assign(fields);
			expect(JSON.parse(String(posts[0].body))).toEqual({ adviser_id: 'usr_1', role: 'adviser' });
		}
	});

	it('refuses a role outside the list, and posts nothing', async () => {
		// The old free-text values included: `lead` was migrated away and must not come back in.
		for (const role of ['lead', 'advisor', 'Adviser', 'owner']) {
			const { result, calls } = await assign({ adviser_id: 'usr_1', role });
			expect(result.status, role).toBe(400);
			expect(result.data?.error, role).toBe('Pick a role.');
			expect(calls, role).toHaveLength(0);
		}
	});

	it('refuses without a person, and posts nothing', async () => {
		const { result, calls } = await assign({ role: 'support' });
		expect(result.status).toBe(400);
		expect(result.data?.error).toBe('Pick a person.');
		expect(calls).toHaveLength(0);
	});

	it('shows the API’s own refusal, with its status', async () => {
		// "That person is not a member of staff…" and the like come back as a 404 `detail`.
		const { result } = await assign(
			{ adviser_id: 'usr_9', role: 'paraplanner' },
			{ contactStatus: 404 }
		);
		expect(result.status).toBe(404);
		expect(result.data?.error).toBe('nope');
	});

	it('stays behind contacts:write', async () => {
		await expect(
			actions.assignAdviser!({
				request: submit({ adviser_id: 'usr_1', role: 'adviser' }),
				locals: localsWith('instructions:write'),
				params: { uid: 'con_9b2e' },
				fetch: (() => {
					throw new Error('the guard let an unauthorised action reach the API');
				}) as never
			} as never)
		).rejects.toMatchObject({ status: 303, location: '/clients/con_9b2e' });
	});
});

describe('the record MOUNTS the advisers and logins dialogs', () => {
	/**
	 * THE DEFECT. The `/people` page (now deleted) was retired for two popouts the record's Team menu
	 * and portal-access badge open. The endpoint, the form actions and the loader's `members` fetch all moved across — the
	 * MOUNT did not. `client-record.svelte` renders those dialogs off `actions?.assignAdviser` and
	 * friends, so with none passed both opened read-only: the advisers dialog listed who was already
	 * assigned and offered no way to add one, and assigning an adviser was impossible from the
	 * record. No 403 and no broken form, which is exactly why it read as a permissions problem.
	 *
	 * Read off the source: the dialogs live inside a closed `Dialog.Content`, which renders nothing
	 * at SSR, so asserting the rendered body would pass for the wrong reason.
	 */
	const mount = readFileSync(
		join(import.meta.dirname, '../../../lib/components/clients/client-record-page.svelte'),
		'utf8'
	);

	it('passes the four actions the dialogs gate on', () => {
		for (const action of ['assignAdviser', 'unassignAdviser', 'linkUser', 'unlinkUser']) {
			expect(mount, action).toContain(`${action}: act('${action}')`);
		}
	});

	it('passes the picker its same-origin URLs, not a first-paint list', () => {
		// Members and countries load when the popout / combobox opens (`/pickers/…`), so first
		// paint of the record does not pay for the firm's people or the country vocabulary.
		expect(mount).toContain("members: data.canManage ? '/pickers/members' : undefined");
		expect(mount).toContain("countries: '/pickers/countries'");
		// Models are an ACCOUNT's concern: the picker fetches when the account record's model
		// control opens, not on the client record at all.
		const accountRecord = readFileSync(
			join(import.meta.dirname, '../../../lib/components/accounts/account-record.svelte'),
			'utf8'
		);
		expect(accountRecord).toContain("fetch('/pickers/models')");
	});

	it('keeps them behind contacts:write, with the rest of the client-editing actions', () => {
		// They write `crm.client_advisers` and `crm.contact_users`, which the API gates on
		// contacts:write — not on the money-movement key the same mount also injects. Anchored
		// between two actions already known to sit in that block, since `canIllustrate` appears
		// earlier in `links` and cannot delimit it.
		const block = mount.slice(
			mount.indexOf("updateClient: act('updateClient')"),
			mount.indexOf("deleteNote: act('deleteNote')")
		);
		expect(block).toContain('assignAdviser');
		expect(block).toContain('linkUser');
	});
});

describe('the Details band', () => {
	it('posts to the overview actions via an absolute prefix', () => {
		// Relative `../?/` from `/clients/{uid}/details` pops both `details` AND `{uid}`, landing
		// on `/clients` (a 404). The actions live once on the overview `+page.server`; Details
		// names them with an absolute `/clients/{uid}?/` that cannot be ambiguous.
		const page = readFileSync(join(import.meta.dirname, 'details/+page.svelte'), 'utf8');
		expect(page).toContain('actionsPrefix={`/clients/${record.detail.contact.uid}?/`}');
	});
});
