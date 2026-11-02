/**
 * The root layout loader — what it AWAITS and what it STREAMS. The platform portal's own test
 * records the reasoning in full; this pins the same contract for the admin shell: the wrong-portal
 * redirect is decided before the response starts, the bell is a streamed promise that fails open,
 * and navigation does not call `GET /terms/outstanding`.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+layout.server';

type LoadEvent = Parameters<typeof load>[0];
type Loaded = Exclude<Awaited<ReturnType<typeof load>>, void>;

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function api(over: Partial<Record<'notifications', () => Promise<Response>>> = {}) {
	return vi.fn(async (input: string | URL | Request) => {
		const url = String(input);
		if (url.includes('/terms/outstanding')) {
			throw new Error(`terms must not be fetched on navigation: ${url}`);
		}
		if (url.includes('/notifications'))
			return (over.notifications ?? (() => json({ items: [{ uid: 'n1' }], unread_count: 1 })))();
		throw new Error(`unexpected fetch: ${url}`);
	});
}

function event(over: { fetch?: ReturnType<typeof api>; portal?: string | null } = {}) {
	const cookies = { delete: vi.fn() };
	return {
		url: new URL('http://ops.test/tenants'),
		locals: {
			user: { id: 'user_1', email: 'ops@graphene.test' },
			accessToken: 'token',
			permissions: ['system'],
			role: 'operations',
			portal: 'portal' in over ? over.portal : 'operations',
			brand: null,
			traceId: 't',
			spanId: 's'
		},
		cookies,
		fetch: over.fetch ?? api()
	} as unknown as LoadEvent & { cookies: typeof cookies };
}

const run = async (e: LoadEvent) => (await load(e)) as Loaded;

describe('the admin root layout', () => {
	it('does not fetch terms or redirect a signed-in session', async () => {
		const e = event();
		const data = await run(e);
		expect(data.user?.email).toBe('ops@graphene.test');
		const fetch = e.fetch as unknown as ReturnType<typeof api>;
		expect(fetch.mock.calls.some(([u]) => String(u).includes('/terms/outstanding'))).toBe(false);
	});

	it('refuses a session that resolved to a different portal', async () => {
		const e = event({ portal: 'platform' });
		await expect(load(e)).rejects.toMatchObject({ status: 303, location: '/login?wrong_portal=1' });
		expect(e.cookies.delete).toHaveBeenCalled();
	});

	it('streams the bell rather than awaiting it', async () => {
		const e = event({ fetch: api({ notifications: () => new Promise<Response>(() => {}) }) });
		const data = await run(e);
		expect(data.notifications).toBeInstanceOf(Promise);
		expect(data.notificationsUnread).toBeInstanceOf(Promise);
		expect(data.permissions).toEqual(['system']);
	});

	it('resolves the bell, and fails it open to empty rather than rejecting', async () => {
		const ok = await run(event());
		expect(await ok.notifications).toEqual([{ uid: 'n1' }]);
		expect(await ok.notificationsUnread).toBe(1);

		const down = await run(
			event({
				fetch: api({
					notifications: async () => {
						throw new Error('ECONNREFUSED');
					}
				})
			})
		);
		expect(await down.notifications).toEqual([]);
		expect(await down.notificationsUnread).toBe(0);
	});
});
