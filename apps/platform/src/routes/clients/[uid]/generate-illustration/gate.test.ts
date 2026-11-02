/**
 * The generate-illustration gate (the six splits — docs/api.md): generating takes
 * `illustrations:write`, split out of documents:write because it calls a paid external engine — so
 * the proxy must refuse before it forwards anything.
 *
 * This is what survives of the record's Illustrations section: the listing and composer pages are
 * gone (filed illustrations are read from the client's documents), and the drawer on the record
 * posts here.
 */
import { describe, expect, it, vi } from 'vitest';
import { POST as generateProxy } from './+server';

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

const locals = (permissions: string[], user: unknown = { id: 'u_1' }) => ({
	user,
	permissions,
	role: null,
	accessToken: 'tok'
});

describe('the generate POST proxy gate', () => {
	const run = (permissions: string[], user: unknown = { id: 'u_1' }) => {
		let forwarded = false;
		// URL-aware, because the handler makes TWO calls now: it resolves the client's uid to the
		// serial the engine endpoint takes, then posts. Only the second is "reaching the engine",
		// which is what the refusal cases below assert never happens.
		const fetch = vi.fn(async () => {
			forwarded = true;
			return json({ uid: 'doc_1' }, 201);
		});
		const response = generateProxy({
			locals: locals(permissions, user),
			params: { uid: 'con_9b2e' },
			request: new Request('http://portal.test', { method: 'POST', body: '{}' }),
			fetch
		} as never);
		return { response, wasForwarded: () => forwarded };
	};

	it('refuses documents:write alone before forwarding anything to the engine', async () => {
		const { response, wasForwarded } = run(['documents:write']);
		expect((await response).status).toBe(403);
		expect(wasForwarded()).toBe(false);
	});

	it('refuses a caller who is not signed in', async () => {
		const { response, wasForwarded } = run(['illustrations:write'], null);
		expect((await response).status).toBe(401);
		expect(wasForwarded()).toBe(false);
	});

	it('forwards for illustrations:write', async () => {
		const { response, wasForwarded } = run(['illustrations:write']);
		expect((await response).status).toBe(200);
		expect(wasForwarded()).toBe(true);
	});
});
