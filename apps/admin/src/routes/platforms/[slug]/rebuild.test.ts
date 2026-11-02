/**
 * `?/runRebuild` — the recovery when a provider's state db and its DuckLake catalogue disagree.
 *
 * interlace records what it has built in a SQLite state db beside the project; the tables live in
 * the catalogue. Anything that empties the catalogue without telling interlace (a `db:reset`, a
 * restored Postgres, a hand-dropped schema) leaves the daemon reading a model it believes it
 * built — `Table with name … does not exist`.
 *
 * Sync cannot fix that: change detection consults the same state db and therefore skips exactly
 * the models that are missing. Hence a separate action, and hence these tests — the point is that
 * it is a DIFFERENT call, guarded, with the API's refusal surfaced rather than swallowed.
 */
import { describe, expect, it, vi } from 'vitest';
import { actions } from './+page.server';

const locals = (permissions: string[] = ['platforms:write']) =>
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

function harness(response: Response = json({ environment: { views: 43 }, gc: {}, run: {} })) {
	const calls: { url: string; method: string }[] = [];
	const fetch = vi.fn(async (url: string | URL, init?: RequestInit) => {
		calls.push({ url: String(url), method: init?.method ?? 'GET' });
		return response;
	}) as unknown as typeof globalThis.fetch;
	return { calls, fetch };
}

const run = (fetch: typeof globalThis.fetch, slug = 'seccl') =>
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	(actions.runRebuild as any)({ params: { slug }, locals: locals(), fetch });

describe('?/runRebuild', () => {
	it('POSTs the rebuild endpoint, not sync', async () => {
		// The whole reason this action exists: /sync would consult the same stale state.
		const { calls, fetch } = harness();
		await run(fetch);
		expect(calls).toHaveLength(1);
		expect(calls[0].method).toBe('POST');
		expect(calls[0].url).toMatch(/\/admin\/platforms\/seccl\/rebuild$/);
		expect(calls[0].url).not.toMatch(/\/sync$/);
	});

	it('returns the daemon ledger so the banner can say what happened', async () => {
		const { fetch } = harness();
		const result = await run(fetch);
		expect(result).toMatchObject({ rebuildStarted: { environment: { views: 43 } } });
	});

	it('surfaces the API 404 for an unknown platform', async () => {
		// The action used to refuse locally, from a slug map that lived beside two fixture datasets.
		// `_known_provider` is the registry now — it resolves a slug or a provider key and 404s the
		// rest — so an unknown platform is the API's answer rather than this file's opinion.
		const { calls, fetch } = harness(json({ detail: 'Unknown provider' }, 404));
		const result = await run(fetch, 'not-a-platform');
		expect(result).toMatchObject({ status: 404 });
		expect(calls).toHaveLength(1);
	});

	it('surfaces the API refusal rather than reporting a rebuild that never started', async () => {
		// A 503 here means the daemon is unreachable — and the operator has to know the environment
		// was NOT dropped, because the alternative reading is "my warehouse is mid-rebuild".
		const { fetch } = harness(json({ detail: 'pipeline service not configured' }, 503));
		const result = await run(fetch);
		expect(result).toMatchObject({ status: 503 });
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		expect(String((result as any).data.firmsError)).toContain('pipeline service');
	});
});
