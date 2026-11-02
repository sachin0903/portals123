/**
 * The home page's `completeTask` action — the ONE action on this route, and the one that used to
 * put a raw form field straight into an API path without checking for a session first.
 */
import { describe, expect, it, vi } from 'vitest';
import { actions } from './+page.server';

type ActionArg = Parameters<typeof actions.completeTask>[0];

function event(uid: string, user: { id: string } | null = { id: 'u1' }) {
	const calls: string[] = [];
	const fetch = vi.fn(async (input: unknown) => {
		calls.push(String(input));
		return new Response('{}', { status: 200 });
	});
	const body = new FormData();
	body.append('uid', uid);
	return {
		arg: {
			request: new Request('http://portal.test/?/completeTask', { method: 'POST', body }),
			locals: { user, accessToken: user ? 'tok' : null },
			fetch
		} as unknown as ActionArg,
		calls
	};
}

describe('home — completeTask', () => {
	it('posts to the task’s own completion endpoint', async () => {
		const { arg, calls } = event('V1StGXR8Z5jd');
		expect(await actions.completeTask(arg)).toEqual({ ok: true });
		expect(calls).toEqual(['http://127.0.0.1:8000/tasks/V1StGXR8Z5jd/complete']);
	});

	it('refuses a uid that would retarget the POST, before any upstream call', async () => {
		// Verified: `new URL(\`${api}/tasks/${uid}/complete\`)` with this value resolves to
		// `${api}/messaging/thread/messages?/complete` — the caller's bearer, any route they name.
		const { arg, calls } = event('x/../../messaging/thread/messages?');
		expect(await actions.completeTask(arg)).toMatchObject({ status: 400 });
		expect(calls).toHaveLength(0);
	});

	it('requires a session rather than sending `Bearer null` upstream', async () => {
		const { arg, calls } = event('V1StGXR8Z5jd', null);
		expect(await actions.completeTask(arg)).toMatchObject({ status: 401 });
		expect(calls).toHaveLength(0);
	});
});
