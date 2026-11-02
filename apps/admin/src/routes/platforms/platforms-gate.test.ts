/**
 * The /platforms section's gate (permission audit M1, re-keyed for the six splits).
 *
 * The nav hides Platforms without `platforms:read`, but the URLs themselves were open: the index,
 * the whole /platforms/[slug] tree (including the SQL console page) and the console's POST proxy
 * (`/platforms/[slug]/query/run`) all loaded for any signed-in operator. The API gates each fetch
 * regardless — these tests pin the SvelteKit half, mirroring the book layout's gate tests.
 *
 * The SQL console now takes its OWN slug (`platforms:query`, split out of `platforms:read` —
 * docs/api.md "six splits"): the query page load and the run proxy demand it, while every other
 * section of the tree stays on `platforms:read`.
 */
import { describe, expect, it, vi } from 'vitest';
import { load as index } from './+page.server';
import { load as slugLayout } from './[slug]/+layout.server';
import { load as queryPage } from './[slug]/query/+page.server';
import { POST as runProxy } from './[slug]/query/run/+server';

type GateLocals = {
	user: unknown;
	accessToken: string | null;
	permissions: string[];
	role: string | null;
};
const locals = (permissions: string[], role: string | null = null): GateLocals => ({
	user: { id: 'u-ops', email: 'ops@graphene.test' },
	accessToken: 'tok',
	permissions,
	role
});

const okJson = (body: unknown) =>
	new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'content-type': 'application/json' }
	});

/* eslint-disable @typescript-eslint/no-explicit-any */
const runIndex = (args: object) => (index as any)(args);
const runLayout = (args: object) => (slugLayout as any)(args);
const runQueryPage = (args: object) => (queryPage as any)(args);
const runQuery = (args: object) => (runProxy as any)(args);
/* eslint-enable @typescript-eslint/no-explicit-any */

describe('the platforms index gate', () => {
	it('refuses an operator without platforms:read (tenants admin is not platform ops)', async () => {
		await expect(
			runIndex({ locals: locals(['tenants:read', 'tenants:write']), fetch: () => okJson([]) })
		).rejects.toMatchObject({ status: 403 });
	});

	it('admits platforms:read', async () => {
		const result = await runIndex({
			locals: locals(['platforms:read']),
			fetch: () => okJson([])
		});
		expect(result.statuses).toEqual([]);
	});

	it('sends a signed-out visitor to /login before any permission talk', async () => {
		await expect(
			runIndex({ locals: { ...locals(['platforms:read']), user: null }, fetch: () => okJson([]) })
		).rejects.toMatchObject({ status: 303, location: '/login' });
	});
});

describe('the /platforms/[slug] layout gate (covers overview, models, runs, query, lineage, streams)', () => {
	it('refuses an operator without platforms:read before fetching anything', async () => {
		// The permission check comes before the live call, so a refused operator's request never
		// reaches the API — `fetch` would throw if it were consulted.
		await expect(
			runLayout({ locals: locals(['tenants:read']), params: { slug: 'sei' } })
		).rejects.toMatchObject({ status: 403 });
	});

	it('falls back to the fixture when the live overview fails, and says why', async () => {
		// This was "a concept slug needs no API at all", pinned on 'sei'. Every platform in the
		// registry is live now (SEI joined `LIVE_PROVIDERS` when its pipeline was wired), so there is
		// no concept-only slug left to assert that with — the fixture fall-through survives for
		// exactly one case, a live provider whose daemon is unreachable, which is what this pins.
		const result = await runLayout({
			locals: locals(['platforms:read']),
			params: { slug: 'sei' },
			fetch: () =>
				new Response(JSON.stringify({ detail: 'pipeline service not configured' }), {
					status: 503,
					headers: { 'content-type': 'application/json' }
				})
		});
		expect(result.slug).toBe('sei');
		expect(result.dataset).toBeNull();
		expect(String(result.liveError)).toContain('pipeline service');
	});

	it('still 404s an unknown slug — but only after the permission check', async () => {
		// THE GUARD IS THE API's NOW. It used to be `isPlatformSlug`, a lookup in a module of
		// checked-in fixture data — so which platform pages existed was decided by a TypeScript
		// mock rather than by the registry the API serves. `_known_provider` resolves a slug or a
		// provider key and 404s the rest; the layout passes that through.
		const notFound = () =>
			new Response(JSON.stringify({ detail: 'Unknown provider' }), {
				status: 404,
				headers: { 'content-type': 'application/json' }
			});
		await expect(
			runLayout({
				locals: locals(['platforms:read']),
				params: { slug: 'nonsense' },
				fetch: notFound
			})
		).rejects.toMatchObject({ status: 404 });
		// The ORDER is what matters and is unchanged: without permission the request never leaves,
		// so an unauthorised operator learns nothing about which slugs exist.
		const unreached = vi.fn(notFound);
		await expect(
			runLayout({ locals: locals([]), params: { slug: 'nonsense' }, fetch: unreached })
		).rejects.toMatchObject({ status: 403 });
		expect(unreached).not.toHaveBeenCalled();
	});
});

describe('the Query console page gate — platforms:query, not platforms:read', () => {
	it('refuses platforms:read alone — yesterday’s key no longer opens the console', () => {
		// The narrowed-role posture: an observer keeps overview/models/runs (the layout gate),
		// but the console page itself 403s without the console slug.
		expect(() => runQueryPage({ locals: locals(['platforms:read']) })).toThrowError(
			expect.objectContaining({ status: 403 })
		);
		// platforms:write does not imply it either (write⇒read never crosses actions).
		expect(() => runQueryPage({ locals: locals(['platforms:write']) })).toThrowError(
			expect.objectContaining({ status: 403 })
		);
	});

	it('admits platforms:query', () => {
		expect(runQueryPage({ locals: locals(['platforms:query']) })).toEqual({});
	});

	it('sends a signed-out visitor to /login before any permission talk', () => {
		expect(() =>
			runQueryPage({ locals: { ...locals(['platforms:query']), user: null } })
		).toThrowError(expect.objectContaining({ status: 303, location: '/login' }));
	});
});

describe('the SQL console run proxy gate', () => {
	// A POST runs no layout load, so neither the [slug] layout's gate nor the query page's covers
	// this route — it must carry its own. Without it, any signed-in operator could submit SQL to
	// the lake console (the API's platforms:query gate was the only thing standing).
	const request = { json: async () => ({ sql: 'SELECT 1' }) };

	it('refuses an operator without platforms:query before forwarding any SQL', async () => {
		let forwarded = false;
		// platforms:read — the console's OLD key — must no longer forward, nor the tenants slugs.
		for (const held of [['tenants:read', 'tenants:write'], ['platforms:read']]) {
			await expect(
				runQuery({
					locals: locals(held),
					params: { slug: 'seccl' },
					request,
					fetch: () => {
						forwarded = true;
						return okJson({ rows: [] });
					}
				})
			).rejects.toMatchObject({ status: 403 });
		}
		expect(forwarded).toBe(false);
	});

	it('forwards for platforms:query and passes the API response through', async () => {
		const response = await runQuery({
			locals: locals(['platforms:query']),
			params: { slug: 'seccl' },
			request,
			fetch: () => okJson({ rows: [], truncated: false })
		});
		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({ rows: [], truncated: false });
	});

	it('refuses without the console permission before the request leaves', async () => {
		// The proxy no longer knows which slugs exist — `_known_provider` does, and an unknown one
		// comes back as the API's own 404. What this still pins is the ORDER: an operator with no
		// permission learns nothing, because nothing is asked on their behalf.
		const unreached = vi.fn(() => okJson({ rows: [], truncated: false }));
		await expect(
			runQuery({ locals: locals([]), params: { slug: 'nonsense' }, request, fetch: unreached })
		).rejects.toMatchObject({ status: 403 });
		expect(unreached).not.toHaveBeenCalled();
	});
});
