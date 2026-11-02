/**
 * The proxy middle-office actions — the platform's client-record actions, re-hosted on the ops
 * book with `?tenant=<uid>` on EVERY call that touches the firm (reads and writes alike; the
 * `/admin/tenants/by-uid/{uid}` resolution is the one operations-plane read).
 *
 * What these tests pin, mirroring apps/platform .../clients/[id]/page.server.test.ts:
 *  · the withdrawal control (cash leg, netted of pending claims — the highest-severity check);
 *  · every refusal that must happen BEFORE any POST (inactive client, bad dates, bad amounts,
 *    failed basis reads, malformed bank details);
 *  · `book:write` is the pen — `book:read` alone is bounced off every action;
 *  · the three affordances the shared record renders but the product owner did NOT approve
 *    (notes) refuse server-side and never touch the API. Close/deactivate are not ported.
 */
import { describe, expect, it } from 'vitest';
import { actions } from './+page.server';

const TENANT = { id: 4, uid: 't_9f2ab1', name: 'Caldwell Wealth' };
const ACCOUNT = 7;
const CASH = 2_000;
const INVESTED = 48_000;

const locals = (permissions: string[] = ['book:read', 'book:write']) =>
	({
		user: { id: 'u-ops' },
		accessToken: 'tok',
		permissions,
		role: null
	}) as unknown as App.Locals;

// The route param is the firm's UID (`/tenants/{uid}/…`), not its serial id — so this is the uid
// itself, and the actions resolve the numeric id from it via `/admin/tenants/by-uid/{uid}`.
const PARAMS = { uid: TENANT.uid, clientUid: 'con_5c1d' };

/** A pending instruction on ACCOUNT unless told otherwise. */
const pending = (over: Record<string, unknown> = {}) => ({
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
});

/** One account's provider position: £2,000 cash (a money-market fund whose asset_type is the
 *  lake's coarse "Stock"), £48,000 invested. */
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

type Call = { url: URL; method: string; headers: Headers; body: unknown };

/** A fake API: the tenant resolution, the contact re-check, the basis reads and every write
 *  endpoint, each overridable per test. Records every call so a test can prove what was (and
 *  wasn't) sent — and that the tenant rode along on all of it. */
function api(
	opts: {
		tenantStatus?: number;
		enabled?: boolean;
		contactStatus?: number;
		holdings?: unknown;
		holdingsStatus?: number;
		pending?: unknown[];
		pendingStatus?: number;
		/** Status for the write the action ends with (POST/PATCH/PUT), 200/201 otherwise. */
		writeStatus?: number;
		/** Body echoed for the terminal write when it succeeds. */
		saved?: unknown;
		/** Status for the signed W-9 / W-8 upload that follows a Compliance save. */
		uploadStatus?: number;
	} = {}
) {
	const calls: Call[] = [];
	const json = (body: unknown, status = 200) =>
		new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
	const refuse = (status: number) => json({ detail: 'API said no' }, status);
	const fetch = (async (url: string | URL, init?: RequestInit) => {
		const u = new URL(String(url));
		const method = init?.method ?? 'GET';
		calls.push({ url: u, method, headers: new Headers(init?.headers), body: init?.body });
		if (u.pathname === `/admin/tenants/by-uid/${TENANT.uid}`)
			return opts.tenantStatus ? refuse(opts.tenantStatus) : json(TENANT);
		if (u.pathname === '/crm/contacts/con_5c1d' && method === 'GET') {
			if (opts.contactStatus) return refuse(opts.contactStatus);
			return json({ contact: { id: 5, enabled: opts.enabled ?? true }, accounts: [] });
		}
		if (u.pathname === '/crm/holdings') {
			if (opts.holdingsStatus) return refuse(opts.holdingsStatus);
			return json(opts.holdings ?? POSITION);
		}
		if (u.pathname === '/firm/contacts/con_5c1d/instructions' && method === 'GET') {
			if (opts.pendingStatus) return refuse(opts.pendingStatus);
			return json(opts.pending ?? []);
		}
		if (u.pathname === '/crm/contacts/con_5c1d/fatca-document') {
			if (opts.uploadStatus)
				return json({ detail: 'Only a W-9 or a W-8 takes a signed form.' }, opts.uploadStatus);
			return json({ fatca_document: { uid: 'doc_w9', name: 'w9-signed.pdf' } });
		}
		// Everything else is a terminal write (PATCH contact, PUT bank/model, POST instruction/cancel).
		if (opts.writeStatus) return refuse(opts.writeStatus);
		return json(opts.saved ?? { ok: true }, method === 'POST' ? 201 : 200);
	}) as unknown as typeof globalThis.fetch;
	return {
		calls,
		fetch,
		writes: () => calls.filter((c) => c.method !== 'GET'),
		posted: () =>
			calls.filter((c) => c.method === 'POST' && c.url.pathname.endsWith('/instructions'))
	};
}

function submit(fields: Record<string, string | File>) {
	const body = new FormData();
	for (const [k, v] of Object.entries(fields)) body.set(k, v);
	return new Request(`http://admin.test/tenants/${TENANT.uid}/book/clients/5`, {
		method: 'POST',
		body
	});
}

type Result = {
	status?: number;
	ok?: boolean;
	cleared?: boolean;
	warning?: string;
	data?: { error?: string; field?: string; action?: string };
};

const run = async (
	name: keyof typeof actions,
	fields: Record<string, string | File>,
	opts: Parameters<typeof api>[0] = {},
	who: App.Locals = locals()
) => {
	const a = api(opts);
	const result = (await actions[name]!({
		request: submit(fields),
		locals: who,
		params: PARAMS,
		fetch: a.fetch
	} as never)) as Result;
	return { result, ...a };
};

const TOMORROW = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);

describe('the pen is book:write', () => {
	const names = Object.keys(actions) as (keyof typeof actions)[];

	it('covers every exported action in this suite’s permission sweep', () => {
		expect(names.sort()).toEqual([
			'addNote',
			'cancelInstruction',
			'createInstruction',
			'setAccountModel',
			'updateBankAccount',
			'updateClient'
		]);
	});

	it('bounces book:read (the proxy VIEW) off every action, before any fetch', async () => {
		for (const name of names) {
			const a = api();
			await expect(
				actions[name]!({
					request: submit({}),
					locals: locals(['book:read']),
					params: PARAMS,
					fetch: a.fetch
				} as never),
				String(name)
			).rejects.toMatchObject({
				status: 303,
				location: `/tenants/${TENANT.uid}/book/clients/con_5c1d`
			});
			expect(a.calls, String(name)).toHaveLength(0);
		}
	});

	it('sends a signed-out visitor to /login', async () => {
		await expect(
			actions.updateClient!({
				request: submit({}),
				locals: { ...locals(), user: null },
				params: PARAMS,
				fetch: api().fetch
			} as never)
		).rejects.toMatchObject({ status: 303, location: '/login' });
	});
});

describe('every call targets the firm', () => {
	it('rides ?tenant=<uid> on every read and write an action makes (bar the tenant lookup)', async () => {
		const { calls } = await run(
			'createInstruction',
			{ kind: 'withdrawal', account_id: String(ACCOUNT), amount: '1' },
			{}
		);
		const book = calls.filter((c) => !c.url.pathname.startsWith('/admin/tenants/by-uid/'));
		expect(book.length).toBeGreaterThanOrEqual(4); // re-check, holdings, pending, POST
		for (const c of book) {
			expect(c.url.searchParams.get('tenant'), `${c.method} ${c.url.pathname}`).toBe(TENANT.uid);
		}
	});

	it('never asks the operations plane which firm this is — the uid IS the route', async () => {
		// This replaces a test for "the firm couldn't be resolved, so nothing changed". That failure
		// mode no longer exists here: the book actions take the uid straight from the URL, so there is
		// no lookup between the guard and the write that could fail halfway. A wrong uid is refused by
		// the API on the book call itself, with nothing written locally either way.
		const { calls } = await run('updateClient', { type: 'individual', first_name: 'Theo' }, {});
		expect(calls.filter((c) => c.url.pathname.startsWith('/admin/tenants/'))).toHaveLength(0);
		for (const c of calls) expect(c.url.searchParams.get('tenant')).toBe(TENANT.uid);
	});
});

describe('?/updateClient', () => {
	it("PATCHes the staff endpoint with ?tenant=, leaving a person's name to the database", async () => {
		const { result, writes } = await run(
			'updateClient',
			{ type: 'individual', first_name: 'Theo', surname: 'Caldwell' },
			{ saved: { name: 'Theo Caldwell' } }
		);
		expect(result).toMatchObject({ action: 'updateClient', ok: true });
		const [w] = writes();
		expect(w.method).toBe('PATCH');
		expect(w.url.pathname).toBe('/firm/contacts/con_5c1d');
		expect(w.url.searchParams.get('tenant')).toBe(TENANT.uid);
		const body = JSON.parse(String(w.body));
		expect(body).not.toHaveProperty('name');
		expect(body.first_name).toBe('Theo');
	});

	it('keeps an organisation on its own name field', async () => {
		const { writes } = await run(
			'updateClient',
			{ type: 'organisation', name: 'Caldwell Holdings Ltd', first_name: 'ignored' },
			{ saved: { name: 'Caldwell Holdings Ltd' } }
		);
		const body = JSON.parse(String(writes()[0].body));
		expect(body.name).toBe('Caldwell Holdings Ltd');
		expect(body.first_name).toBeNull();
	});

	it('rejects a malformed email before sending anything — not even the tenant lookup', async () => {
		const { result, calls } = await run('updateClient', {
			type: 'individual',
			email: 'not-an-email'
		});
		expect(result.status).toBe(400);
		expect(result.data?.error).toContain('email');
		expect(calls).toHaveLength(0);
	});

	it('warns instead of claiming success when a requested clear did not happen', async () => {
		const { result } = await run(
			'updateClient',
			{ type: 'individual', first_name: 'Theo', surname: 'Caldwell', mobile: '' },
			{ saved: { name: 'Theo Caldwell', mobile: '07700 900000' } }
		);
		expect(result.warning).toContain('mobile');
	});

	it('sends the other number as a string, and a blank as null', async () => {
		const kept = await run(
			'updateClient',
			{ type: 'individual', phone: '0117 900 0001' },
			{ saved: {} }
		);
		expect(JSON.parse(String(kept.writes()[0].body)).phone).toBe('0117 900 0001');

		const cleared = await run(
			'updateClient',
			{ type: 'individual', phone: '' },
			{ saved: { phone: null } }
		);
		expect(JSON.parse(String(cleared.writes()[0].body)).phone).toBeNull();
		expect(cleared.result.warning).toBeUndefined();
	});

	it('PATCHes source of wealth from the Compliance card without wiping contact details', async () => {
		// The shared record's Compliance form posts `type` + SoW and nothing else. The old
		// always-sent body dropped `source_of_wealth` and sent `first_name: null`.
		const { writes } = await run(
			'updateClient',
			{ type: 'individual', source_of_wealth: 'Gift' },
			{ saved: { source_of_wealth: 'Gift' } }
		);
		const body = JSON.parse(String(writes()[0].body));
		expect(body.source_of_wealth).toBe('Gift');
		expect(body).not.toHaveProperty('first_name');
		expect(body).not.toHaveProperty('email');
	});

	it('clears the W-9 or W-8 and its date when the blank is picked', async () => {
		const cleared = await run(
			'updateClient',
			{ type: 'individual', fatca_status: '', fatca_status_was: 'w8' },
			{ saved: {} }
		);
		const body = JSON.parse(String(cleared.writes()[0].body));
		expect(body.fatca_status).toBeNull();
		expect(body.fatca_certified_at).toBeNull();

		const kept = await run(
			'updateClient',
			{ type: 'individual', fatca_status: '', fatca_status_was: '' },
			{ saved: {} }
		);
		expect(JSON.parse(String(kept.writes()[0].body))).not.toHaveProperty('fatca_status');
	});

	it('does not PATCH source of wealth on an organisation', async () => {
		const { writes } = await run(
			'updateClient',
			{ type: 'organisation', name: 'Caldwell Trust', source_of_wealth: 'Gift' },
			{ saved: { name: 'Caldwell Trust' } }
		);
		const body = JSON.parse(String(writes()[0].body));
		expect(body).not.toHaveProperty('source_of_wealth');
	});

	it('surfaces the API’s refusal rather than toasting a save it did not earn', async () => {
		const { result } = await run(
			'updateClient',
			{ type: 'individual', first_name: 'Theo' },
			{ writeStatus: 403 }
		);
		expect(result.status).toBe(403);
		expect(result.data?.error).toBe('API said no');
	});
});

describe('?/updateClient — the signed W-9 or W-8', () => {
	const pdf = (name = 'w9-signed.pdf') => new File(['%PDF-1.7'], name, { type: 'application/pdf' });
	const uploads = (calls: Call[]) =>
		calls.filter((c) => c.url.pathname === '/crm/contacts/con_5c1d/fatca-document');

	it('uploads the file with ?tenant= after the PATCH has saved the answer', async () => {
		const { result, writes } = await run('updateClient', {
			type: 'individual',
			us_person: 'Yes',
			fatca_status: 'w9',
			fatca_status_was: '',
			fatca_document: pdf()
		});
		expect(result).toMatchObject({ action: 'updateClient', ok: true });
		expect(writes().map((c) => `${c.method} ${c.url.pathname}`)).toEqual([
			'PATCH /firm/contacts/con_5c1d',
			'POST /crm/contacts/con_5c1d/fatca-document'
		]);
		const upload = writes()[1];
		expect(upload.url.searchParams.get('tenant')).toBe(TENANT.uid);
		expect(upload.headers.get('authorization')).toBe('Bearer tok');
		expect(upload.headers.has('content-type')).toBe(false);
		const file = (upload.body as FormData).get('file') as File;
		expect(file.name).toBe('w9-signed.pdf');
		expect(await file.text()).toBe('%PDF-1.7');
	});

	it('uploads nothing when the PATCH fails', async () => {
		const { result, calls } = await run(
			'updateClient',
			{ type: 'individual', fatca_status: 'w9', fatca_status_was: '', fatca_document: pdf() },
			{ writeStatus: 422 }
		);
		expect(result.status).toBe(422);
		expect(uploads(calls)).toHaveLength(0);
	});

	it('uploads nothing when the save clears the answer or leaves another code', async () => {
		for (const fatca_status of ['', 'w8_cure']) {
			const { result, calls } = await run('updateClient', {
				type: 'individual',
				fatca_status,
				fatca_status_was: 'w9',
				fatca_document: pdf()
			});
			expect(result, fatca_status).toMatchObject({ ok: true });
			expect(uploads(calls), fatca_status).toHaveLength(0);
		}
	});

	it('uploads nothing when no file was chosen', async () => {
		const { calls } = await run('updateClient', {
			type: 'individual',
			fatca_status: 'w9',
			fatca_status_was: 'w9',
			fatca_document: new File([], '', { type: 'application/octet-stream' })
		});
		expect(uploads(calls)).toHaveLength(0);
	});

	it('says the answer is saved when only the upload fails', async () => {
		const { result, writes } = await run(
			'updateClient',
			{ type: 'individual', fatca_status: 'w8', fatca_status_was: 'w8', fatca_document: pdf() },
			{ uploadStatus: 409 }
		);
		expect(writes().map((c) => c.method)).toEqual(['PATCH', 'POST']);
		expect(result.status).toBe(409);
		expect(result.data?.field).toBe('fatca_document');
		expect(result.data?.error).toBe(
			'The answer is saved, but the form did not upload: Only a W-9 or a W-8 takes a signed form.'
		);
	});
});

describe('?/updateBankAccount', () => {
	const good = {
		bank_scheme: 'bacs',
		bank_name: 'T Caldwell',
		bank_sort_code: '20 00 00',
		bank_account_number: '1234567'
	};

	it('PUTs the canonical shape (not what was keyed) with ?tenant=', async () => {
		const { writes } = await run('updateBankAccount', good);
		expect(writes()).toHaveLength(1);
		const [w] = writes();
		expect(w.method).toBe('PUT');
		expect(w.url.pathname).toBe('/crm/contacts/con_5c1d/bank-account');
		expect(w.url.searchParams.get('tenant')).toBe(TENANT.uid);
		expect(JSON.parse(String(w.body))).toEqual({
			name: 'T Caldwell',
			sort_code: '20-00-00',
			account_number: '01234567'
		});
	});

	it('refuses a malformed sort code without calling the API at all', async () => {
		const { result, calls } = await run('updateBankAccount', { ...good, bank_sort_code: '2000' });
		expect(calls).toHaveLength(0);
		expect(result.status).toBe(400);
		expect(result.data?.field).toBe('sort_code');
		// The message must never carry the digits back.
		expect(result.data?.error).not.toContain('2000');
	});

	it('clears the details when every field is blank', async () => {
		const { result, writes } = await run('updateBankAccount', {
			bank_scheme: 'bacs',
			bank_name: '',
			bank_sort_code: '',
			bank_account_number: ''
		});
		expect(JSON.parse(String(writes()[0].body))).toEqual({
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
		const { writes } = await run('updateBankAccount', { ...good, bank_method: 'faster_payment' });
		expect(JSON.parse(String(writes()[0].body))).toEqual({
			name: 'T Caldwell',
			sort_code: '20-00-00',
			account_number: '01234567',
			method: 'faster_payment'
		});
	});

	it('passes the API refusal through', async () => {
		const { result } = await run('updateBankAccount', good, { writeStatus: 400 });
		expect(result.status).toBe(400);
		expect(result.data?.error).toBe('API said no');
	});
});

describe('?/setAccountModel', () => {
	it('associates a model with the account, with ?tenant=', async () => {
		const { writes } = await run('setAccountModel', { account_uid: 'acc_7', model_id: '3' });
		const [w] = writes();
		expect(w.method).toBe('PUT');
		expect(w.url.pathname).toBe('/crm/accounts/acc_7/model');
		expect(w.url.searchParams.get('tenant')).toBe(TENANT.uid);
		expect(JSON.parse(String(w.body))).toEqual({ model_id: 3 });
	});

	it('clears the mandate on an empty selection — advisory is a real choice, not a missing one', async () => {
		const { result, writes } = await run('setAccountModel', { account_uid: 'acc_7', model_id: '' });
		expect(JSON.parse(String(writes()[0].body))).toEqual({ model_id: null });
		expect(result.cleared).toBe(true);
	});

	it('refuses without an account, never guessing one and never fetching', async () => {
		const { result, calls } = await run('setAccountModel', { model_id: '3' });
		expect(calls).toHaveLength(0);
		expect(result.status).toBe(400);
	});

	it('passes the 409 through when a pending model claim blocks the change', async () => {
		const { result } = await run(
			'setAccountModel',
			{ account_uid: 'acc_7', model_id: '' },
			{ writeStatus: 409 }
		);
		expect(result.status).toBe(409);
		expect(result.data?.error).toBe('API said no');
	});
});

describe('?/createInstruction — the withdrawal control', () => {
	const create = (fields: Record<string, string>, opts: Parameters<typeof api>[0] = {}) =>
		run('createInstruction', fields, opts);

	it('refuses a cash withdrawal larger than the cash leg, and posts nothing', async () => {
		const { result, posted } = await create({
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

	it('allows a withdrawal within the cash leg, POSTing with ?tenant=', async () => {
		const { result, posted } = await create({
			kind: 'withdrawal',
			account_id: String(ACCOUNT),
			amount: String(CASH)
		});
		expect(result).toMatchObject({ action: 'createInstruction', ok: true });
		expect(posted()).toHaveLength(1);
		expect(posted()[0].url.searchParams.get('tenant')).toBe(TENANT.uid);
	});

	it('checks a model withdrawal against what is invested, not the cash or the total', async () => {
		const over = await create({
			kind: 'model_withdrawal',
			account_id: String(ACCOUNT),
			amount: '60000'
		});
		expect(over.result.status).toBe(400);
		expect(over.result.data?.error).toContain('invested in the model');
		expect(over.posted()).toHaveLength(0);

		const ok = await create({
			kind: 'model_withdrawal',
			account_id: String(ACCOUNT),
			amount: String(INVESTED)
		});
		expect(ok.posted()).toHaveLength(1);
	});

	it('counts what pending instructions have already claimed, so the leg can’t be spent twice', async () => {
		const { result, posted } = await create(
			{ kind: 'withdrawal', account_id: String(ACCOUNT), amount: String(CASH) },
			{ pending: [pending({ amount: CASH })] }
		);
		expect(result.status).toBe(400);
		expect(result.data?.error).toContain('already committed to pending instructions');
		expect(result.data?.error).toContain('£0.00');
		expect(posted()).toHaveLength(0);
	});

	it('treats a pending ad hoc fee as a claim on the same cash', async () => {
		const { result, posted } = await create(
			{ kind: 'withdrawal', account_id: String(ACCOUNT), amount: '1' },
			{ pending: [pending({ kind: 'adhoc_fee', amount: CASH })] }
		);
		expect(result.status).toBe(400);
		expect(posted()).toHaveLength(0);
	});

	it('refuses when the account has no provider position at all — fail closed', async () => {
		const { result, posted } = await create(
			{ kind: 'withdrawal', account_id: String(ACCOUNT), amount: '1' },
			{ holdings: [] }
		);
		expect(result.status).toBe(400);
		expect(posted()).toHaveLength(0);
	});

	it('refuses when either basis read fails — "we can’t tell" must not mean "go ahead"', async () => {
		for (const opts of [{ holdingsStatus: 500 }, { pendingStatus: 500 }] as const) {
			const { result, posted } = await create(
				{ kind: 'withdrawal', account_id: String(ACCOUNT), amount: '1' },
				opts
			);
			expect(result.status).toBe(502);
			expect(result.data?.error).toContain('wasn’t raised');
			expect(posted()).toHaveLength(0);
		}
	});

	it('refuses any instruction against an inactive client, before reading holdings', async () => {
		const { result, calls, posted } = await create(
			{ kind: 'withdrawal', account_id: String(ACCOUNT), amount: '1' },
			{ enabled: false }
		);
		expect(result.status).toBe(409);
		expect(result.data?.error).toContain('inactive');
		expect(calls.some((c) => c.url.pathname === '/crm/holdings')).toBe(false);
		expect(posted()).toHaveLength(0);
	});

	it('refuses when the client re-check itself fails', async () => {
		const { result, posted } = await create(
			{ kind: 'expected_in', account_id: String(ACCOUNT), amount: '100' },
			{ contactStatus: 500 }
		);
		expect(result.status).toBeGreaterThanOrEqual(500);
		expect(result.data?.error).toContain('nothing was recorded');
		expect(posted()).toHaveLength(0);
	});

	it('rejects a malformed due date instead of letting the API 500 on the SQL cast', async () => {
		for (const due_date of ['tomorrow', '31/01/2027', '2027-1-5']) {
			const { result, posted } = await create({
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
		const backdated = await create({
			kind: 'expected_in',
			account_id: String(ACCOUNT),
			amount: '100',
			due_date: lastWeek
		});
		expect(backdated.result).toMatchObject({ ok: true });
		expect(backdated.posted()).toHaveLength(1);
		expect(JSON.parse(String(backdated.posted()[0]?.body)).due_date).toBe(lastWeek);

		const withdrawal = await create({
			kind: 'withdrawal',
			account_id: String(ACCOUNT),
			amount: '100',
			due_date: '2020-01-01'
		});
		expect(withdrawal.result.status).toBe(400);
		expect(withdrawal.result.data?.error).toContain('past');
		expect(withdrawal.posted()).toHaveLength(0);
	});

	it('rejects a non-positive or non-numeric amount without any fetch', async () => {
		for (const amount of ['0', '-100', 'lots']) {
			const { result, calls } = await create({
				kind: 'withdrawal',
				account_id: String(ACCOUNT),
				amount
			});
			expect(result.status, amount).toBe(400);
			expect(calls).toHaveLength(0);
		}
	});

	it('leaves money coming IN unconstrained by what is held', async () => {
		const { result, posted } = await create({
			kind: 'expected_in',
			account_id: String(ACCOUNT),
			amount: '250000',
			due_date: TOMORROW
		});
		expect(result).toMatchObject({ ok: true });
		expect(posted()).toHaveLength(1);
	});

	it('surfaces the API’s own refusal rather than claiming success', async () => {
		const { result } = await create(
			{ kind: 'expected_in', account_id: String(ACCOUNT), amount: '100' },
			{ writeStatus: 400 }
		);
		expect(result.status).toBe(400);
		expect(result.data?.error).toBe('API said no');
	});
});

describe('?/cancelInstruction', () => {
	it('POSTs the cancel with ?tenant=', async () => {
		const { result, writes } = await run('cancelInstruction', { uid: 'ins_x' });
		expect(result).toMatchObject({ action: 'cancelInstruction', ok: true });
		const [w] = writes();
		expect(w.method).toBe('POST');
		expect(w.url.pathname).toBe('/firm/instructions/ins_x/cancel');
		expect(w.url.searchParams.get('tenant')).toBe(TENANT.uid);
	});

	it('refuses without an instruction, and never fetches', async () => {
		const { result, calls } = await run('cancelInstruction', {});
		expect(result.status).toBe(400);
		expect(calls).toHaveLength(0);
	});

	it('passes the API refusal through', async () => {
		const { result } = await run('cancelInstruction', { uid: 'ins_x' }, { writeStatus: 409 });
		expect(result.status).toBe(409);
		expect(result.data?.error).toBe('API said no');
	});
});

describe('the affordances the product owner did NOT approve', () => {
	// Note-taking is the firm's own decision about its client. If this test starts failing
	// because someone wired a real call, that is a product decision to take back first.
	it.each([['addNote']] as const)(
		'%s refuses with 403 and makes no API call at all',
		async (name) => {
			const { result, calls } = await run(name, { enabled: 'false', body: 'a note' });
			expect(result.status).toBe(403);
			expect(result.data?.error).toContain('not available here');
			expect(calls).toHaveLength(0);
		}
	);
});
