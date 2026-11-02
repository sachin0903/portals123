/**
 * Server-side auth gates on the Integrations surface (list + detail), mirroring
 * `tenants/[uid]/write-gates.test.ts`'s pattern.
 *
 * Every `load` refuses an unauthenticated caller with a redirect and a caller missing
 * `integrations:read` with a 403 — in both cases before touching the API. Every write action
 * refuses a caller missing `integrations:write` with `fail(403, …)`, again without ever calling
 * `fetch` — the API would refuse it too, but the point of a server-side gate is that the request
 * never leaves this process for a caller who cannot possibly be allowed it.
 */
import { describe, expect, it, vi } from 'vitest';
import { load as listLoad, actions as listActions } from './+page.server';
import { load as detailLoad, actions as detailActions } from './[uid]/+page.server';

const locals = (permissions: string[]) => ({
	user: { id: 'u-ops', email: 'ops@graphene.test' },
	accessToken: 'tok',
	permissions,
	role: null as string | null
});
const READ_ONLY = locals(['integrations:read']);
const WRITER = locals(['integrations:read', 'integrations:write']);

const okJson = (body: unknown = {}) =>
	new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'content-type': 'application/json' }
	});

const request = (fields: Record<string, string | string[]>) => ({
	formData: async () => {
		const data = new FormData();
		for (const [k, v] of Object.entries(fields)) {
			for (const one of Array.isArray(v) ? v : [v]) data.append(k, one);
		}
		return data;
	}
});

/** Runs one action twice: read-only must 403 without touching the API; a writer must reach it. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function expectGated(action: any, event: Record<string, unknown>) {
	let touched = 0;
	const fetch = () => {
		touched += 1;
		return okJson();
	};

	const refused = await action({ ...event, locals: READ_ONLY, fetch });
	expect(refused).toMatchObject({ status: 403 });
	expect(String((refused as { data: { error?: string } }).data.error)).toContain(
		'integrations:write'
	);
	expect(touched).toBe(0);

	const allowed = await action({ ...event, locals: WRITER, fetch });
	expect(allowed).not.toMatchObject({ status: 403 });
	expect(touched).toBeGreaterThan(0);
}

describe('the /integrations list loader gate', () => {
	it('bounces an unauthenticated caller to /login', async () => {
		const fetch = vi.fn(async () => okJson([]));
		await expect(
			listLoad({
				locals: { ...READ_ONLY, user: null },
				fetch,
				url: new URL('http://ops.test/integrations')
			} as never)
		).rejects.toMatchObject({ status: 303, location: '/login' });
		expect(fetch).not.toHaveBeenCalled();
	});

	it('403s without integrations:read — and never calls the API', async () => {
		const fetch = vi.fn(async () => okJson([]));
		await expect(
			listLoad({
				locals: locals(['tenants:read']),
				fetch,
				url: new URL('http://ops.test/integrations')
			} as never)
		).rejects.toMatchObject({ status: 403 });
		expect(fetch).not.toHaveBeenCalled();
	});
});

describe('the /integrations/[uid] loader gate', () => {
	const params = { uid: 'p_1' };

	it('bounces an unauthenticated caller to /login', async () => {
		const fetch = vi.fn(async () => okJson({}));
		await expect(
			detailLoad({
				locals: { ...READ_ONLY, user: null },
				fetch,
				params,
				url: new URL('http://ops.test/integrations/p_1')
			} as never)
		).rejects.toMatchObject({ status: 303, location: '/login' });
		expect(fetch).not.toHaveBeenCalled();
	});

	it('403s without integrations:read — and never calls the API', async () => {
		const fetch = vi.fn(async () => okJson({}));
		await expect(
			detailLoad({
				locals: locals(['tenants:read']),
				fetch,
				params,
				url: new URL('http://ops.test/integrations/p_1')
			} as never)
		).rejects.toMatchObject({ status: 403 });
		expect(fetch).not.toHaveBeenCalled();
	});
});

describe('write actions refuse integrations:read and admit integrations:write', () => {
	it('list — ?/update', async () => {
		await expectGated(listActions.update, {
			request: request({
				uid: 'p_1',
				status: 'approved',
				reason: '',
				firm_id: '5',
				enabled: 'on'
			})
		});
	});

	it('detail — ?/updateIntegration', async () => {
		await expectGated(detailActions.updateIntegration, {
			params: { uid: 'p_1' },
			request: request({ status: 'approved', reason: '', firm_id: '5', enabled: 'on' })
		});
	});
});
