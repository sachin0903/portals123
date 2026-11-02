/**
 * `/tasks` inbox — first paint is the two task windows. Assignee catalogues load when the
 * create dialog opens.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';
import { GET as loadPickers } from './pickers/+server';

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

function harness(handler: (url: string) => Response = () => json([])) {
	const calls: string[] = [];
	const fetch = vi.fn(async (input: unknown) => {
		const url = String(input);
		calls.push(url);
		return handler(url);
	});
	return { fetch, calls };
}

const reader = {
	user: { id: 'u1' },
	permissions: ['tasks:read'],
	role: null,
	accessToken: 't'
};

const manager = { ...reader, permissions: ['tasks:read', 'tasks:write'] };

describe('/tasks load', () => {
	it('fetches the two inbox windows and not the create-dialog pickers', async () => {
		const { fetch, calls } = harness((url) => {
			if (url.includes('/tasks')) return json([{ uid: 'tsk_1', id: 1, status: 'open' }]);
			return json([]);
		});
		const data = (await load({
			locals: manager,
			fetch,
			url: new URL('http://portal.test/tasks'),
			depends: () => {}
		} as never)) as { inbox: Promise<{ tasks: unknown[] }> };
		const inbox = await data.inbox;
		expect(inbox.tasks).toHaveLength(1);
		expect(calls.filter((url) => url.includes('/tasks')).length).toBe(2);
		expect(calls.some((url) => url.includes('/firm/members'))).toBe(false);
		expect(calls.some((url) => url.includes('/firm/roles'))).toBe(false);
		expect(calls.some((url) => url.includes('/documents/clients'))).toBe(false);
	});
});

describe('/tasks/pickers', () => {
	it('loads members, roles and clients for the create dialog', async () => {
		const { fetch, calls } = harness((url) => {
			if (url.includes('/firm/members'))
				return json([{ user_id: 'u', enabled: true, email: 'a@b.c' }]);
			if (url.includes('/firm/roles'))
				return json([{ slug: 'adviser', name: 'Adviser', portal: 'platform' }]);
			if (url.includes('/documents/clients')) return json([{ contact_id: 1 }]);
			return json([]);
		});
		const res = await loadPickers({ locals: manager, fetch } as never);
		const body = await res.json();
		expect(body.members).toEqual([{ user_id: 'u', enabled: true, email: 'a@b.c' }]);
		expect(body.roles).toEqual([{ slug: 'adviser', name: 'Adviser', portal: 'platform' }]);
		expect(body.clients).toEqual([{ contact_id: 1 }]);
		expect(calls.some((url) => url.includes('/firm/members?kind=staff'))).toBe(true);
	});

	it('refuses without tasks:write', async () => {
		const { fetch, calls } = harness();
		const res = await loadPickers({ locals: reader, fetch } as never);
		expect(res.status).toBe(403);
		expect(calls).toEqual([]);
	});
});
