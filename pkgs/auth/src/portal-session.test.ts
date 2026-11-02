import { describe, expect, it, vi } from 'vitest';
import type { WorkOS } from '@workos-inc/node';
import { validatePortalSession } from './portal-session';

const TOKEN = `x.${Buffer.from(JSON.stringify({ sid: 'session_123' })).toString('base64url')}.x`;

function workos() {
	const revokeSession = vi.fn(async () => undefined);
	return {
		client: { userManagement: { revokeSession } } as unknown as WorkOS,
		revokeSession
	};
}

function json(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

describe('validatePortalSession', () => {
	it('allows only the active portal resolved by the API', async () => {
		const w = workos();
		const fetch = vi.fn(async () => json({ portal: 'platform' }));

		expect(
			await validatePortalSession(w.client, TOKEN, 'platform', {
				apiUrl: 'http://api.test',
				fetch
			})
		).toBe('allowed');
		expect(fetch).toHaveBeenCalledWith('http://api.test/me/portal', {
			headers: { Authorization: `Bearer ${TOKEN}`, 'x-graphene-portal': 'platform' }
		});
		expect(w.revokeSession).not.toHaveBeenCalled();
	});

	it('refuses an inactive membership and revokes the new WorkOS session', async () => {
		const w = workos();

		expect(
			await validatePortalSession(w.client, TOKEN, 'platform', {
				apiUrl: 'http://api.test',
				fetch: vi.fn(async () => json({ portal: null }))
			})
		).toBe('denied');
		expect(w.revokeSession).toHaveBeenCalledWith({ sessionId: 'session_123' });
	});

	it('fails closed and revokes the session when membership cannot be checked', async () => {
		const w = workos();

		expect(
			await validatePortalSession(w.client, TOKEN, 'platform', {
				apiUrl: 'http://api.test',
				fetch: vi.fn(async () => {
					throw new Error('API unavailable');
				})
			})
		).toBe('unavailable');
		expect(w.revokeSession).toHaveBeenCalledWith({ sessionId: 'session_123' });
	});
});
