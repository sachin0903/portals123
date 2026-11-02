import { describe, expect, it, vi } from 'vitest';
import { actions, load } from './+page.server';
import { GET as searchClients } from './clients/+server';

type AddArg = Parameters<typeof actions.addProfile>[0];
type AddNewArg = Parameters<typeof actions.addNewProfile>[0];
type SetArg = Parameters<typeof actions.setRoles>[0];

function event(fields: Record<string, string | string[]>) {
	const body = new FormData();
	for (const [key, value] of Object.entries(fields)) {
		for (const item of Array.isArray(value) ? value : [value]) body.append(key, item);
	}
	const fetch = vi.fn(async () => new Response(JSON.stringify({ email_delivered: true })));
	return {
		fetch,
		event: {
			request: new Request('http://portal.test/profiles', { method: 'POST', body }),
			locals: {
				user: { id: 'staff', email: 'staff@firm.test' },
				accessToken: 'token',
				permissions: ['profiles:write', 'contacts:write', 'contacts:create'],
				role: 'administrator'
			}
		}
	};
}

describe('Profiles actions', () => {
	it('creates a profile with the complete client role set', async () => {
		const e = event({
			contact_uid: 'contact-1',
			role_slugs: ['client', 'trustee']
		});
		await actions.addProfile({ ...e.event, fetch: e.fetch } as unknown as AddArg);
		const [url, init] = e.fetch.mock.calls[0] as unknown as [string, { body: string }];
		expect(url).toContain('/firm/contacts/contact-1/portal-invite');
		expect(JSON.parse(init.body)).toMatchObject({
			role_slugs: ['client', 'trustee']
		});
		expect(init.body).not.toContain('"portal"');
	});

	it('creates a new client-linked profile from identity details', async () => {
		const e = event({
			first_name: 'Ada',
			last_name: 'Lovelace',
			email: 'ada@example.test',
			role_slugs: ['client']
		});
		await actions.addNewProfile({ ...e.event, fetch: e.fetch } as unknown as AddNewArg);
		const [url, init] = e.fetch.mock.calls[0] as unknown as [string, { body: string }];
		expect(url).toContain('/firm/members');
		expect(JSON.parse(init.body)).toEqual({
			email: 'ada@example.test',
			first_name: 'Ada',
			last_name: 'Lovelace',
			role_slugs: ['client']
		});
	});

	it('replaces roles only on the client membership', async () => {
		const e = event({ user_id: 'profile-1', role_slugs: ['client', 'delegate'] });
		await actions.setRoles({ ...e.event, fetch: e.fetch } as unknown as SetArg);
		const [url, init] = e.fetch.mock.calls[0] as unknown as [string, { body: string }];
		expect(url).toContain('/firm/members/profile-1/portals/client');
		expect(JSON.parse(init.body)).toEqual({ role_slugs: ['client', 'delegate'] });
	});
});

describe('Profiles load', () => {
	it('does not fetch the contact catalogue on first paint', async () => {
		const calls: string[] = [];
		const fetch = vi.fn(async (input: unknown) => {
			calls.push(String(input));
			if (String(input).includes('/firm/members')) {
				return new Response(JSON.stringify({ items: [], total: 0 }), {
					headers: { 'Content-Type': 'application/json' }
				});
			}
			return new Response(JSON.stringify([]), {
				headers: { 'Content-Type': 'application/json' }
			});
		});
		const data = (await load({
			locals: {
				user: { id: 'staff' },
				accessToken: 'token',
				permissions: ['profiles:read', 'profiles:write', 'contacts:write'],
				role: null
			},
			fetch,
			url: new URL('http://portal.test/profiles')
		} as never)) as { directory: Promise<{ profiles: unknown[] }> };
		const directory = await data.directory;
		expect(directory.profiles).toEqual([]);
		expect(calls.some((url) => url.includes('/crm/contacts'))).toBe(false);
		expect(calls.some((url) => url.includes('/firm/members'))).toBe(true);
		expect(calls.some((url) => url.includes('/firm/roles'))).toBe(true);
	});
});

describe('/profiles/clients', () => {
	it('typeaheads crm/contacts with a limit', async () => {
		const calls: string[] = [];
		const fetch = vi.fn(async (input: unknown) => {
			calls.push(String(input));
			return new Response(
				JSON.stringify({ items: [{ uid: 'con_1', name: 'Ada', email: 'ada@x.test' }] }),
				{ headers: { 'Content-Type': 'application/json' } }
			);
		});
		const res = await searchClients({
			locals: {
				user: { id: 'staff' },
				accessToken: 'token',
				permissions: ['profiles:write', 'contacts:write'],
				role: null
			},
			fetch,
			url: new URL('http://portal.test/profiles/clients?q=ada')
		} as never);
		expect(await res.json()).toEqual([{ value: 'con_1', label: 'Ada', hint: 'ada@x.test' }]);
		expect(calls[0]).toContain('/crm/contacts');
		expect(calls[0]).toContain('q=ada');
		expect(calls[0]).toContain('limit=20');
	});

	it('refuses without contacts:write', async () => {
		const fetch = vi.fn();
		const res = await searchClients({
			locals: {
				user: { id: 'staff' },
				accessToken: 'token',
				permissions: ['profiles:write'],
				role: null
			},
			fetch,
			url: new URL('http://portal.test/profiles/clients')
		} as never);
		expect(res.status).toBe(403);
		expect(fetch).not.toHaveBeenCalled();
	});
});
