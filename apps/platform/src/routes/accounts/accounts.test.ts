/**
 * The accounts routes after the book extraction (@graphene/ui book/accounts).
 *
 * The pages are thin mounts of the shared components, so what is left app-side — and what
 * these tests pin — is the loaders: the listing's fail-soft empty book, and the record's LAYOUT
 * (the account + permission flags, nothing else) plus the section loaders that fill each band.
 * Holdings mapping (the growth-fraction derivation and its absurd-base clamp, which exist because
 * the lake's growth_pct once rendered as +6,303%) lives on Overview and Holdings; the ledger
 * lives on Transactions, so a date filter cannot re-read the lake.
 *
 * The transactions case is the one worth stating: this loader filtered an invented fixture corpus
 * by `account_id`, so most of a firm's accounts showed an empty Transactions band and a few showed
 * rows belonging to nobody. It reads `/crm/transactions?account_id=` now, and these cases pin the
 * request as well as the rows — a loader that fetches the wrong id fails soft and silently.
 */
import { describe, expect, it } from 'vitest';
import { load as listLoad } from './+page.server';
import { load as detailLoad } from './[uid]/+layout.server';
import { load as overviewLoad } from './[uid]/+page.server';
import { load as holdingsLoad } from './[uid]/holdings/+page.server';

type ListEvent = Parameters<typeof listLoad>[0];
type DetailEvent = Parameters<typeof detailLoad>[0];
type OverviewEvent = Parameters<typeof overviewLoad>[0];
type HoldingsEvent = Parameters<typeof holdingsLoad>[0];

const user = { id: 'user_1' };

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});

const ACCOUNT = {
	id: 2,
	uid: 'seccl_AXF5AC13E',
	name: 'Oakden SIPP',
	status: 'active',
	product_type: 'SIPP',
	is_pension: true,
	custodian: 'sei',
	scheme_administrator: 'morgan-lloyd',
	client: {
		id: 4,
		uid: 'cli_oakden',
		type: 'individual',
		first_name: 'Max',
		surname: 'Oakden',
		bank_account: null
	}
};

const parentAccount = async () => ({
	account: ACCOUNT,
	canReadInstructions: true,
	canMoveMoney: true,
	loadError: false
});

/** What the streamed bands hand back, for the tests that read it. `as never` was the old
 *  escape hatch and it made every property read resolve to `never`, so a genuine type error
 *  in one of these tests could not be told from the cast. */
type StreamedBand = { holdings: { gainPct: number; [k: string]: unknown }[] } & Record<
	string,
	unknown
>;

describe('the /accounts loader', () => {
	// SvelteKit types a load's return as possibly `void`; this loader always returns data or
	// throws, so narrow once here — and the book is STREAMED (a promise the page resolves), so it is
	// awaited and flattened here to keep these cases about the fetches rather than the plumbing.
	const run = async (e: ListEvent) => {
		const loaded = (await listLoad(e)) as Exclude<Awaited<ReturnType<typeof listLoad>>, void>;
		return { ...loaded, ...(await loaded.book) };
	};

	/** The listing is server-driven now, so the loader reads the query out of the URL. */
	const listEvent = (fetch: (url: string | URL | Request) => Promise<Response>, search = '') =>
		({
			locals: { user },
			fetch,
			url: new URL(`http://test/accounts${search}`)
		}) as unknown as ListEvent;

	const page = (items: unknown[], total = items.length, facets = {}) =>
		json({ items, total, facets });

	it('renders whatever the API returns (RLS decides the book)', async () => {
		const result = await run(listEvent(async () => page([ACCOUNT])));
		expect(result.accounts).toHaveLength(1);
		expect(result.matchCount).toBe(1);
	});

	it('fails soft to an empty book rather than a 500', async () => {
		const result = await run(listEvent(async () => json({ detail: 'boom' }, 502)));
		expect(result.accounts).toEqual([]);
		expect(result.matchCount).toBe(0);
	});

	it('forwards the URL search and filters to the API, and asks for a page', async () => {
		// The whole point of the move: filtering one page client-side would report "no account
		// for Halloway" when it means "not on this page".
		let requested = '';
		await run(
			listEvent(async (url) => {
				if (String(url).includes('/crm/accounts')) requested = String(url);
				return page([ACCOUNT]);
			}, '?q=halloway&status=active&model=42&offset=50')
		);
		expect(requested).toContain('q=halloway');
		expect(requested).toContain('status=active');
		expect(requested).toContain('model=42');
		expect(requested).toContain('offset=50');
		expect(requested).toContain('limit=');
	});

	it('builds Type and Model options from the tenant’s enabled catalogues', async () => {
		const result = await run(
			listEvent(async (url) => {
				const requested = String(url);
				if (requested.includes('/catalog/products')) {
					return json([
						{ id: 1, product_type: 'SIPP', name: 'Pension', enabled: true },
						{ id: 2, product_type: 'GIA', name: 'Investment', enabled: true },
						{ id: 3, product_type: 'SIPP', name: 'Another pension', enabled: true },
						{ id: 4, product_type: 'ISA', name: 'Disabled ISA', enabled: false }
					]);
				}
				if (requested.includes('/catalog/models')) {
					return json([
						{ id: 42, name: 'Growth 60' },
						{ id: 7, name: 'Cautious 30' }
					]);
				}
				return page([ACCOUNT]);
			})
		);

		expect(result.productTypeOptions).toEqual([
			{ value: 'GIA', label: 'GIA' },
			{ value: 'SIPP', label: 'SIPP' }
		]);
		expect(result.modelOptions).toEqual([
			{ value: '7', label: 'Cautious 30' },
			{ value: '42', label: 'Growth 60' }
		]);
	});

	it('does not forward a param that is not one of its filters', async () => {
		let requested = '';
		await run(
			listEvent(async (url) => {
				if (String(url).includes('/crm/accounts')) requested = String(url);
				return page([]);
			}, '?utm_source=email')
		);
		expect(requested).not.toContain('utm_source');
	});

	it('bounces an unauthenticated caller to /login', async () => {
		await expect(
			listLoad({
				locals: { user: null },
				fetch: async () => page([]),
				url: new URL('http://test/accounts')
			} as unknown as ListEvent)
		).rejects.toMatchObject({ status: 303, location: '/login' });
	});
});

describe('the /accounts/[uid] layout', () => {
	const event = (
		account: Record<string, unknown> = ACCOUNT,
		permissions: string[] = ['instructions:write']
	) => {
		const urls: string[] = [];
		return {
			urls,
			event: {
				locals: { user, accessToken: 't', permissions, role: null },
				params: { uid: 'seccl_AXF5AC13E' },
				depends: () => {},
				fetch: async (url: string | URL | Request) => {
					const u = String(url);
					urls.push(u);
					if (u.includes('/crm/holdings')) throw new Error('layout must not fetch holdings');
					if (u.includes('/crm/transactions')) {
						throw new Error('layout must not fetch transactions');
					}
					if (u.includes('/instructions')) throw new Error('layout must not fetch instructions');
					if (u.includes('/contributions')) {
						throw new Error('layout must not fetch contributions');
					}
					if (u.includes('/catalog/fee-packages')) {
						throw new Error('layout must not fetch fee packages');
					}
					if (u.includes('/beneficiaries')) {
						throw new Error('layout must not fetch beneficiaries');
					}
					if (u.includes('/firm/money-actions')) {
						return json({
							disabled: [],
							require_justification: ['transaction.payment']
						});
					}
					if (u.includes('/reviews/policies')) {
						return json([
							{
								action_key: 'client.bank',
								mode: 'four_eye',
								require_justification: true
							}
						]);
					}
					return json(account);
				}
			} as unknown as DetailEvent
		};
	};

	// The detail loader STREAMS its band, so the rows it is asked about sit behind that promise
	// — the same shape the listing's `run` above unwraps.
	const run = async (e: DetailEvent) => {
		const loaded = (await detailLoad(e)) as Exclude<Awaited<ReturnType<typeof detailLoad>>, void>;
		return { ...loaded, ...(await loaded.band) };
	};

	it('loads the account and nothing else', async () => {
		const { urls, event: e } = event();
		const result = await run(e);
		expect(result.account.uid).toBe(ACCOUNT.uid);
		expect(result.holdings).toEqual([]);
		expect(result.instructions).toEqual([]);
		expect(result.contributionSummary).toBeNull();
		expect(urls).toHaveLength(3);
		expect(urls[0]).toContain('/crm/accounts/seccl_AXF5AC13E');
		expect(urls[1]).toContain('/firm/money-actions');
		expect(urls.some((u) => u.includes('/reviews/policies'))).toBe(true);
		expect(result.justificationRequired).toEqual(['client.bank', 'transaction.payment']);
		expect(urls.some((u) => u.includes('/crm/holdings'))).toBe(false);
		expect(urls.some((u) => u.includes('/instructions'))).toBe(false);
		expect(urls.some((u) => u.includes('/contributions'))).toBe(false);
	});

	it('does not fetch transactions on the record layout', async () => {
		const result = await run(event().event);
		expect(result.transactions).toEqual([]);
		expect(result.loadError).toBe(false);
	});

	it('gates drawdown on a pension MORGAN LLOYD administers, not on the wrapper alone', async () => {
		expect((await run(event().event)).canDrawdown).toBe(true);

		const gia = await run(event({ ...ACCOUNT, is_pension: false, product_type: 'GIA' }).event);
		expect(gia.canDrawdown).toBe(false);

		// THE CASE THE OLD GATE GOT WRONG. A real SIPP, custodied and administered somewhere
		// Morgan Lloyd has never heard of, offered a Drawdown button that answers 409 at the
		// vendor — which reads as an outage rather than as an account with no scheme.
		const elsewhere = await run(event({ ...ACCOUNT, scheme_administrator: null }).event);
		expect(elsewhere.canDrawdown).toBe(false);
		expect(elsewhere.canPushToMorganLloyd).toBe(false);
		// …but it is still a pension, and a pension is payable into wherever it is administered.
		// These were one flag, so narrowing drawdown silently took the SIPP payment type off with it.
		expect(elsewhere.canContribute).toBe(true);

		const secclSipp = await run(
			event({ ...ACCOUNT, custodian: 'seccl', scheme_administrator: 'morgan-lloyd' }).event
		);
		expect(secclSipp.canDrawdown).toBe(false);
		expect(secclSipp.canPushToMorganLloyd).toBe(false);
		expect(secclSipp.canContribute).toBe(true);
	});

	it('reads the shelf first and falls back to the wrapper set', async () => {
		// `is_pension` is the column a firm can see and correct; the wrapper set is nine
		// disagreeing copies. A mount over an older API that sends neither still works.
		const shelfSaysNo = await run(event({ ...ACCOUNT, is_pension: false }).event);
		expect(shelfSaysNo.canDrawdown).toBe(false);

		const noColumn = { ...ACCOUNT } as Record<string, unknown>;
		delete noColumn.is_pension;
		expect((await run(event(noColumn).event)).canDrawdown).toBe(true);
	});

	it('gates the push on contacts:sync_morgan_lloyd, not contacts:write or instructions:write', async () => {
		// Minting a person and a scheme in a live CRM is not editing a client record; minting a
		// live drawdown session is money movement. One role legitimately holds only the sync.
		const editor = await run(event(ACCOUNT, ['contacts:write']).event);
		expect(editor.canPushToMorganLloyd).toBe(false);
		expect(editor.canDrawdown).toBe(false);

		const writer = await run(event(ACCOUNT, ['contacts:sync_morgan_lloyd']).event);
		expect(writer.canPushToMorganLloyd).toBe(true);
		expect(writer.canDrawdown).toBe(false);

		const mover = await run(event(ACCOUNT, ['instructions:write']).event);
		expect(mover.canPushToMorganLloyd).toBe(false);
		expect(mover.canDrawdown).toBe(true);
	});

	it('gates drawdown and the transfer entry point on instructions:write, not contacts:write', async () => {
		const narrowed = await run(event(ACCOUNT, ['contacts:write']).event);
		expect(narrowed.canDrawdown).toBe(false);
		expect(narrowed.canMoveMoney).toBe(false);

		const mover = await run(event(ACCOUNT, ['instructions:write']).event);
		expect(mover.canDrawdown).toBe(true);
		expect(mover.canMoveMoney).toBe(true);
	});

	it('propagates a 404 from the API as a page 404', async () => {
		const notFound = {
			locals: { user, accessToken: 't', permissions: [], role: null },
			params: { uid: 'no-such-account' },
			depends: () => {},
			fetch: async () => json({ detail: 'missing' }, 404)
		} as unknown as DetailEvent;
		await expect(detailLoad(notFound)).rejects.toMatchObject({ status: 404 });
	});
});

describe('the /accounts/[uid] overview', () => {
	const event = (
		holdings: unknown[],
		account: Record<string, unknown> = ACCOUNT,
		permissions: string[] = ['instructions:write', 'instructions:read', 'contributions:read']
	) => {
		const urls: string[] = [];
		return {
			urls,
			event: {
				parent: async () => ({
					account,
					canReadInstructions: permissions.includes('instructions:read'),
					canMoveMoney: permissions.includes('instructions:write'),
					loadError: false
				}),
				locals: { user, accessToken: 't', permissions, role: null },
				params: { uid: 'seccl_AXF5AC13E' },
				fetch: async (url: string | URL | Request) => {
					const u = String(url);
					urls.push(u);
					// The ALLOCATION summary and the RECENT MOVEMENTS window are two reads the overview
					// takes alongside the positions themselves. `/crm/holdings/summary` before
					// `/crm/holdings`, because the second matches the first's path as a prefix.
					if (u.includes('/crm/holdings/summary')) return json([]);
					if (u.includes('/crm/holdings')) return json(holdings);
					if (u.includes('/crm/transactions')) return json([]);
					if (u.includes('/firm/contacts/') && u.includes('/instructions')) return json([]);
					if (u.includes('/catalog/fee-packages')) return json([]);
					if (u.includes('/contributions/summary')) {
						return json({
							tax_year: '2026/27',
							account_gross: 1000,
							count: 1
						});
					}
					if (u.includes('/beneficiaries')) return json([]);
					if (u.includes('/reviews/pending') || u.includes('/reviews/policies')) return json([]);
					throw new Error(`unexpected overview fetch: ${u}`);
				}
			} as unknown as OverviewEvent
		};
	};

	// The overview STREAMS: the loader returns `band` as a promise and the page awaits it. Every
	// case below reads the band's own fields, so this flattens the two into the one object the
	// page sees. `Exclude<…, void>` is for the loader's `redirect()` path; the band keeps its real
	// type, because casting the result to `never` made every `result.holdings` below a type error
	// while the assertions themselves stayed green.
	const run = async (e: OverviewEvent) => {
		const loaded = (await overviewLoad(e)) as Exclude<
			Awaited<ReturnType<typeof overviewLoad>>,
			void
		> & { band: Promise<Record<string, unknown>> };
		return { ...loaded, ...(await loaded.band) } as typeof loaded & StreamedBand;
	};

	it('asks for this account’s holdings and account-scoped instructions', async () => {
		const { urls, event: e } = event([]);
		await run(e);
		expect(urls.some((u) => u.includes('/crm/holdings') && u.includes('account_id=2'))).toBe(true);
		expect(
			urls.some(
				(u) => u.includes('/firm/contacts/cli_oakden/instructions') && u.includes('account_id=2')
			)
		).toBe(true);
		expect(
			urls.some(
				(u) =>
					u.includes('/contributions/summary') &&
					u.includes('contact_id=4') &&
					u.includes('account_id=2')
			)
		).toBe(true);
	});

	it('derives the holding return from growth/cost, not the lake percent', async () => {
		const { holdings } = await run(
			event([
				{
					asset_id: 'fund_1',
					asset_name: 'Fund',
					isin: 'GB00TEST',
					sedol: 'BTEST01',
					currency: 'USD',
					book_cost: 1000,
					growth: 150,
					quantity: 10,
					price: 5,
					value: 1150,
					as_of: '2026-08-22'
				}
			]).event
		);
		expect(holdings[0].gainPct).toBe(0.15);
		expect(holdings[0]).toMatchObject({
			instrumentId: 'fund_1',
			isin: 'GB00TEST',
			sedol: 'BTEST01',
			currency: 'USD',
			bookCost: 1000,
			unrealisedGain: 150,
			asOf: '2026-08-22',
			href: '/accounts/seccl_AXF5AC13E/transactions?isin=GB00TEST'
		});
	});

	it('drops an absurd base to null ("—") rather than rendering +6,303%', async () => {
		const { holdings } = await run(
			event([{ asset_name: 'Fund', book_cost: 1, growth: 100, quantity: 1, price: 1, value: 101 }])
				.event
		);
		expect(holdings[0].gainPct).toBeNull();
	});

	it('maps the contribution summary onto the account card', async () => {
		const { contributionSummary } = await run(event([]).event);
		expect(contributionSummary).toMatchObject({
			taxYear: '2026/27',
			accountGross: 1000,
			count: 1
		});
	});

	it('does not fetch pension extras for a GIA', async () => {
		const { urls, event: e } = event([], { ...ACCOUNT, is_pension: false, product_type: 'GIA' });
		await run(e);
		expect(urls.some((u) => u.includes('/contributions'))).toBe(false);
		expect(urls.some((u) => u.includes('/beneficiaries'))).toBe(false);
	});
});

describe('the /accounts/[uid]/holdings loader', () => {
	const event = (holdings: unknown[]) => {
		const urls: string[] = [];
		return {
			urls,
			event: {
				parent: parentAccount,
				locals: { user, accessToken: 't', permissions: [], role: null },
				fetch: async (url: string | URL | Request) => {
					const u = String(url);
					urls.push(u);
					if (u.includes('/crm/holdings')) return json(holdings);
					if (u.includes('/instructions') || u.includes('/contributions')) {
						throw new Error('holdings must not fetch extras');
					}
					throw new Error(`unexpected holdings fetch: ${u}`);
				}
			} as unknown as HoldingsEvent
		};
	};

	// Same flattening as the overview above, and for the same reason: the band streams, and a
	// result cast to `never` type-errors on every field the assertions read.
	const run = async (e: HoldingsEvent) => {
		const loaded = (await holdingsLoad(e)) as Exclude<
			Awaited<ReturnType<typeof holdingsLoad>>,
			void
		> & { band: Promise<Record<string, unknown>> };
		return { ...loaded, ...(await loaded.band) } as typeof loaded & StreamedBand;
	};

	it('loads this account’s holdings and nothing else', async () => {
		const { urls, event: e } = event([
			{ asset_name: 'Fund', book_cost: 100, growth: 10, quantity: 1, price: 110, value: 110 }
		]);
		const result = await run(e);
		expect(result.holdings).toHaveLength(1);
		expect(result.holdings[0].gainPct).toBe(0.1);
		expect(urls).toHaveLength(1);
		expect(urls[0]).toContain('/crm/holdings');
		expect(urls[0]).toContain('account_id=2');
	});
});
