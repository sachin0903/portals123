/**
 * `/journeys` — the listing loader. Start-menu flows are a write-gated extra; a reader must not
 * wait on the flow catalogue, and a writer must not download drafts they cannot start.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

function harness() {
	const calls: string[] = [];
	const fetch = vi.fn(async (input: unknown) => {
		const url = String(input);
		calls.push(url);
		if (url.includes('/applications')) return json([{ uid: 'app_1' }]);
		if (url.includes('/flows')) return json([{ uid: 'flw_1', status: 'published' }]);
		return json([]);
	});
	return { fetch, calls };
}

describe('/journeys load', () => {
	it('fetches published flows only when the caller can start a journey', async () => {
		const { fetch, calls } = harness();
		const data = (await load({
			locals: {
				user: { id: 'u1' },
				permissions: ['cases:read', 'cases:write'],
				role: null,
				accessToken: 't'
			},
			fetch
		} as never)) as { inbox: Promise<{ startFlows: unknown[] }> };
		const inbox = await data.inbox;
		expect(inbox.startFlows).toEqual([{ uid: 'flw_1', status: 'published' }]);
		expect(calls.some((url) => url.includes('/flows?status=published'))).toBe(true);
		expect(calls.some((url) => /\/flows$/.test(url))).toBe(false);
	});

	it('skips the flow catalogue entirely without cases:write', async () => {
		const { fetch, calls } = harness();
		const data = (await load({
			locals: {
				user: { id: 'u1' },
				permissions: ['cases:read'],
				role: null,
				accessToken: 't'
			},
			fetch
		} as never)) as { inbox: Promise<{ startFlows: unknown[] }> };
		const inbox = await data.inbox;
		expect(inbox.startFlows).toEqual([]);
		expect(calls.some((url) => url.includes('/applications'))).toBe(true);
		expect(calls.some((url) => url.includes('/flows'))).toBe(false);
	});
});
