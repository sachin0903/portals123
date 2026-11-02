/**
 * The overview's two merge loads: what the merge DID, and what it refused.
 *
 * `platform.merge_rejects` exists because a firm with no rows and a firm whose rows were ALL refused
 * look identical from every other surface — the reasons are the only thing that tells the two apart.
 * `platform.merge_runs` (0076) answers the question asked before that one: did the merge run at all?
 * Nothing could answer it, because the ledger reached only `book_{uid}.merge_ledger`, a warehouse
 * relation holding a single run and reachable only from the SQL console.
 *
 * So the states this must never conflate are "nothing was refused", "nothing has run" and "we could
 * not find out" — three different answers that all render as an empty card if the loader is sloppy.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

const locals = () =>
	({
		user: { id: 'u-ops', email: 'ops@graphene.test' },
		accessToken: 'tok',
		permissions: ['platforms:read'],
		role: null
	}) as never;

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const EMPTY_REJECTS = { reasons: [], recent: [], rejected: 0 };
const EMPTY_LEDGER = { runs: [] };

/** Answers each endpoint from its own body, so the two loads cannot be conflated. */
function harness(bodies: { rejects?: Response; ledger?: Response } = {}) {
	const calls: string[] = [];
	const fetch = vi.fn(async (url: string | URL) => {
		const href = String(url);
		calls.push(href);
		if (href.includes('/ledger')) return bodies.ledger ?? json(EMPTY_LEDGER);
		return bodies.rejects ?? json(EMPTY_REJECTS);
	}) as unknown as typeof globalThis.fetch;
	return { calls, fetch };
}

const run = (fetch: typeof globalThis.fetch, slug = 'seccl') =>
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	(load as any)({ locals: locals(), params: { slug }, fetch });

describe('overview — refused rows', () => {
	it('asks the provider for its pooled refusals', async () => {
		const { calls, fetch } = harness();
		await run(fetch);
		expect(calls.some((c) => /\/admin\/platforms\/seccl\/rejects\?limit=25$/.test(c))).toBe(true);
	});

	it('returns the reasons for the page to render', async () => {
		const { fetch } = harness({
			rejects: json({
				reasons: [
					{
						entity: 'transactions',
						reason: 'unmapped type: Expectation/FAD Gross Withdrawal',
						rejected: 113,
						last_seen: '2026-08-06T02:00:00Z',
						tenants: 1,
						tenant_name: 'A Firm'
					}
				],
				recent: [],
				rejected: 113
			})
		});
		const result = await run(fetch);
		expect(result.rejects.rejected).toBe(113);
		expect(result.rejects.reasons[0].entity).toBe('transactions');
	});

	it('distinguishes "could not load" from "nothing was refused"', async () => {
		// The whole point of the ledger is that absence is ambiguous, so its own loader must not
		// reproduce the ambiguity: a failure is null plus a reason, never an empty list.
		const { fetch } = harness({
			rejects: json({ detail: 'pipeline service not configured' }, 503)
		});
		const result = await run(fetch);
		expect(result.rejects).toBeNull();
		expect(String(result.rejectsError)).toContain('pipeline service');
	});

	it('reports an unknown platform as a failure to load, not as an absence', async () => {
		// The slug guard is the API's now (`_known_provider`), so an unknown platform comes back as
		// a 404 — and the rule this file exists for still holds: a failure is null PLUS a reason,
		// never an empty list, because absence is exactly the ambiguity the ledger fixes.
		const notFound = json({ detail: 'Unknown provider' }, 404);
		const { fetch } = harness({ rejects: notFound, ledger: notFound });
		const result = await run(fetch, 'not-a-platform');
		expect(result.rejects).toBeNull();
		expect(result.ledger).toBeNull();
		expect(String(result.rejectsError)).toContain('Unknown provider');
	});
});

describe('overview — merge runs', () => {
	it('asks the provider for its recent runs', async () => {
		const { calls, fetch } = harness();
		await run(fetch);
		expect(calls.some((c) => /\/admin\/platforms\/seccl\/ledger\?limit=10$/.test(c))).toBe(true);
	});

	it('returns the runs with their per-entity detail', async () => {
		const { fetch } = harness({
			ledger: json({
				runs: [
					{
						run_id: 'r-1',
						tenant_uid: 'ten_abc',
						tenant_name: 'A Firm',
						run_at: '2026-08-09T02:00:00Z',
						created: 426,
						updated: 4,
						rejected: 0,
						failed: 0,
						entities: [
							{
								entity: 'models',
								created: 51,
								updated: 0,
								rejected: 0,
								// The counted observations that are deliberately NOT rejects, and so
								// appear in no other table at all.
								reasons: { 'unmodelled model wrapper: SSAS': 18 },
								note: null
							}
						]
					}
				]
			})
		});
		const result = await run(fetch);
		expect(result.ledger.runs[0].created).toBe(426);
		expect(result.ledger.runs[0].entities[0].reasons['unmodelled model wrapper: SSAS']).toBe(18);
	});

	it('distinguishes "could not load" from "nothing has ever run"', async () => {
		const { fetch } = harness({ ledger: json({ detail: 'nope' }, 503) });
		const result = await run(fetch);
		expect(result.ledger).toBeNull();
		expect(String(result.ledgerError)).toContain('nope');
		// The other load must survive its sibling failing — half the answer beats none.
		expect(result.rejects).not.toBeNull();
	});
});
