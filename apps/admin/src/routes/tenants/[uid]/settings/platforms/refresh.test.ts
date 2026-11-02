/**
 * `?/refreshProvider` — re-run ONE firm's pipeline models on the provider's daemon.
 *
 * `book_sink` tags every model it registers for a firm `tenant:{uid}`, so a refresh is one
 * selector covering the whole chain: slice → stage → merge. The tests that matter are about what
 * the operator is TOLD:
 *
 * * `restart_required` means the run selected nothing, and the note says to restart the service;
 * * the refresh writes rows into the firm's book, so it takes tenants:write like enabling does.
 */
import { describe, expect, it, vi } from 'vitest';
import { refreshMessage } from '$lib/platforms/registry';
import { actions } from './+page.server';

const locals = (permissions: string[] = ['tenants:write']) =>
	({
		user: { id: 'u-ops', email: 'ops@graphene.test' },
		accessToken: 'tok',
		permissions,
		role: null
	}) as never;

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});

/** Just the refresh POST — the action has the uid the endpoint takes, so it resolves nothing. */
function harness(refresh: Response = json({ run: {}, selected: 9, restart_required: false })) {
	const calls: { url: string; method: string }[] = [];
	const fetch = vi.fn(async (url: string | URL, init?: RequestInit) => {
		const href = String(url);
		calls.push({ url: href, method: init?.method ?? 'GET' });
		return refresh;
	}) as unknown as typeof globalThis.fetch;
	return { calls, fetch };
}

const request = (provider = 'seccl') =>
	({ formData: async () => new URLSearchParams({ provider }) }) as unknown as Request;

const run = (
	fetch: typeof globalThis.fetch,
	{ permissions, provider }: { permissions?: string[]; provider?: string } = {}
) =>
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	(actions.refreshProvider as any)({
		params: { uid: 't-abc' },
		locals: locals(permissions),
		fetch,
		request: request(provider)
	});

describe('?/refreshProvider', () => {
	it('POSTs the per-tenant refresh, not the whole provider sync', async () => {
		const { calls, fetch } = harness();
		await run(fetch);
		const post = calls.find((c) => c.method === 'POST');
		expect(post?.url).toMatch(/\/admin\/tenants\/[a-z0-9_-]+\/providers\/seccl\/refresh$/);
		// A provider-wide sync would re-run every firm's models to refresh one.
		expect(calls.some((c) => /\/platforms\/seccl\/sync$/.test(c.url))).toBe(false);
	});

	it('says how many data sets the refresh selected', async () => {
		const { fetch } = harness();
		const result = await run(fetch);
		expect(result).toMatchObject({ provider: 'seccl', refreshed: { selected: 9 } });
		expect(refreshMessage(result.refreshed).tone).toBe('default');
		expect(refreshMessage(result.refreshed).text).toContain('9 data sets');
	});

	it('explains a run that selected nothing instead of reporting success', async () => {
		// The failure this whole action has to defend against: 200, no models, no data, no clue.
		//
		// The WORDING moved to `registry.refreshMessage`, shared with the platform page's own
		// per-firm Refresh — they call the same endpoint, and two hand-written descriptions of one
		// outcome is how the same event comes to read as two different events. What the action
		// still owes is the flag itself, unswallowed.
		const { fetch } = harness(json({ run: {}, selected: 0, restart_required: true }));
		const result = await run(fetch);
		expect(result.refreshed.restart_required).toBe(true);
		const message = refreshMessage(result.refreshed);
		expect(message.tone).toBe('destructive');
		expect(message.text).toContain('Restart the provider service');
		expect(message.text).toContain('No data was refreshed');
	});

	it('refuses a read-only operator without touching the API', async () => {
		// Resolving the tenant is itself an API call, so the guard has to come first.
		const { calls, fetch } = harness();
		const result = await run(fetch, { permissions: ['tenants:read'] });
		expect(result).toMatchObject({ status: 403 });
		expect(calls).toHaveLength(0);
	});

	it('surfaces the API refusal rather than a refresh that never ran', async () => {
		const { fetch } = harness(json({ detail: 'pipeline service not configured' }, 503));
		const result = await run(fetch);
		expect(result).toMatchObject({ status: 503 });
		expect(String(result.data.providerError)).toContain('pipeline service');
	});
});
