/**
 * The root layout loader — what it AWAITS and what it STREAMS.
 *
 * Everything awaited here is on the critical path to the first byte of every page: the hook has
 * already paid for the session and `GET /me` before this runs, and the HTML cannot start until it
 * returns. So the contract is: the wrong-portal redirect is decided before the response starts,
 * and the bell and the Messages dot are returned as PROMISES that SvelteKit streams behind the
 * shell. Both must fail open — an empty bell, no dot — because a streamed promise that rejects is
 * a broken layout. Terms are not asked: the login gate was removed, and this loader must not call
 * `GET /terms/outstanding`.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+layout.server';

type LoadEvent = Parameters<typeof load>[0];
type Loaded = Exclude<Awaited<ReturnType<typeof load>>, void>;

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});

/** A stand-in API: two notifications (one unread), something unread in Messages. A terms call is a
 * failure — the login gate is gone, and navigation must not ask. */
function api(over: Partial<Record<'notifications' | 'unread', () => Promise<Response>>> = {}) {
	return vi.fn(async (input: string | URL | Request) => {
		const url = String(input);
		if (url.includes('/terms/outstanding')) {
			throw new Error(`terms must not be fetched on navigation: ${url}`);
		}
		if (url.includes('/notifications'))
			return (
				over.notifications ??
				(() => json({ items: [{ uid: 'n1' }, { uid: 'n2' }], unread_count: 1 }))
			)();
		if (url.includes('/messaging/unread')) return (over.unread ?? (() => json({ unread: true })))();
		throw new Error(`unexpected fetch: ${url}`);
	});
}

function event(over: {
	fetch?: ReturnType<typeof api>;
	pathname?: string;
	permissions?: string[];
	portal?: string | null | undefined;
	signedIn?: boolean;
}) {
	const fetch = over.fetch ?? api();
	const signedIn = over.signedIn ?? true;
	const cookies = { delete: vi.fn() };
	return {
		url: new URL(`http://portal.test${over.pathname ?? '/clients'}`),
		locals: {
			user: signedIn
				? { id: 'user_1', email: 'amy@firm.test', firstName: 'Amy', lastName: 'Blake' }
				: null,
			accessToken: signedIn ? 'token' : null,
			permissions: over.permissions ?? ['messages:read'],
			role: 'adviser',
			portal: 'portal' in over ? over.portal : 'platform',
			branding: null,
			appName: 'Firm',
			traceId: 't',
			spanId: 's'
		},
		cookies,
		fetch
	} as unknown as LoadEvent & { fetch: ReturnType<typeof api>; cookies: typeof cookies };
}

const run = async (e: LoadEvent) => (await load(e)) as Loaded;

describe('what the root layout awaits', () => {
	it('does not fetch terms or redirect a signed-in session', async () => {
		const e = event({});
		const data = await run(e);
		expect(data.user?.email).toBe('amy@firm.test');
		expect(e.fetch.mock.calls.some(([u]) => String(u).includes('/terms/outstanding'))).toBe(false);
	});

	it('refuses a session that resolved to a different portal', async () => {
		const e = event({ portal: 'client' });
		await expect(load(e)).rejects.toMatchObject({ status: 303, location: '/login?wrong_portal=1' });
		expect(e.cookies.delete).toHaveBeenCalled();
	});
});

describe('what the root layout streams', () => {
	it('returns before the bell and the dot have answered', async () => {
		// The bell and the dot never answer. The loader must still return — with promises in those
		// keys — or both round trips are back on the time to first byte.
		const pending = () => new Promise<Response>(() => {});
		const e = event({ fetch: api({ notifications: pending, unread: pending }) });
		const data = await run(e);
		expect(data.notifications).toBeInstanceOf(Promise);
		expect(data.notificationsUnread).toBeInstanceOf(Promise);
		expect(data.chatsUnread).toBeInstanceOf(Promise);
		// And the synchronous keys are all there for the shell's first paint.
		expect(data.user).toEqual({ email: 'amy@firm.test', name: 'Amy Blake' });
		expect(data.permissions).toEqual(['messages:read']);
		expect(data.role).toBe('adviser');
		expect(data.appName).toBe('Firm');
		expect(data.traceId).toBe('t');
		expect(data.spanId).toBe('s');
	});

	it('resolves the bell’s rows and count, and the Messages dot', async () => {
		const data = await run(event({}));
		expect(await data.notifications).toEqual([{ uid: 'n1' }, { uid: 'n2' }]);
		expect(await data.notificationsUnread).toBe(1);
		expect(await data.chatsUnread).toBe(true);
	});

	it('does not ask about Messages for a caller who cannot read them', async () => {
		const e = event({ permissions: ['contacts:read'] });
		const data = await run(e);
		expect(await data.chatsUnread).toBe(false);
		expect(e.fetch.mock.calls.some(([u]) => String(u).includes('/messaging/unread'))).toBe(false);
	});

	it('fails open to an empty bell and no dot, never a rejection', async () => {
		// A rejected streamed promise is a broken layout; these two are best-effort by contract.
		const down = async () => {
			throw new Error('ECONNREFUSED');
		};
		const data = await run(event({ fetch: api({ notifications: down, unread: down }) }));
		expect(await data.notifications).toEqual([]);
		expect(await data.notificationsUnread).toBe(0);
		expect(await data.chatsUnread).toBe(false);

		const refused = async () => json({ detail: 'nope' }, 403);
		const data2 = await run(event({ fetch: api({ notifications: refused, unread: refused }) }));
		expect(await data2.notifications).toEqual([]);
		expect(await data2.notificationsUnread).toBe(0);
		expect(await data2.chatsUnread).toBe(false);
	});

	it('streams resolved empties for a signed-out request, and calls nothing', async () => {
		const e = event({ signedIn: false });
		const data = await run(e);
		expect(data.user).toBeNull();
		expect(await data.notifications).toEqual([]);
		expect(await data.notificationsUnread).toBe(0);
		expect(await data.chatsUnread).toBe(false);
		expect(e.fetch).not.toHaveBeenCalled();
	});
});
