/**
 * The client portal's notification proxies — that every one of them forwards the ACTIVE contact.
 *
 * THE DEFECT. A login linked to more than one client record picks one active client in the nav
 * switcher, and `GET /notifications` narrows to it with `?contact_id=`. Without that parameter the
 * API is scoped by RLS alone, which is EVERY linked contact. The layout's initial load, the badge
 * poll and the all-notifications page all passed it; the bell's own FEED refetch — the call made
 * when the popover opens and on every realtime nudge — did not. So the dropdown merged two
 * clients' notifications ("New message from" two different advisers) while the badge beside it
 * counted one, and the two disagreed on screen.
 *
 * The events proxy is the same rule on the live channel: the parameter rides on the TICKET path,
 * because the ticket is what decides which key the reader listens on.
 *
 * All three assertions are about a parameter that is silent when dropped — the feed still answers,
 * with more rows than the reader asked for — which is why they are pinned here rather than left to
 * a type nothing checks.
 */
import { describe, expect, it, vi } from 'vitest';
import { GET as recent } from './recent/+server';
import { GET as unreadCount } from './unread-count/+server';
import { GET as events } from './events/+server';
import { activeContactId, activeContactParam } from '$lib/active-contact';

vi.mock('$lib/api', () => ({
	apiUrl: () => 'http://api.test',
	authHeaders: (t: string | null) => ({ authorization: `Bearer ${t}` })
}));

/** Captured so the SSE proxy can be asserted on without a stream: it is the TICKET path we care
 *  about, and `privateSseResponse` is what builds the URL from it. */
const sse = vi.hoisted(() => ({ ticketPath: '' }));
vi.mock('@graphene/auth', () => ({
	privateSseResponse: (opts: { ticketPath: string }) => {
		sse.ticketPath = opts.ticketPath;
		return new Response('ok');
	}
}));

type RecentArg = Parameters<typeof recent>[0];

/** Runs a JSON proxy against `query` and hands back the API URL it called. */
async function called(
	handler: typeof recent,
	query: string,
	body: unknown = { items: [] },
	status = 200
) {
	let url = '';
	const fetch = vi.fn(async (u: string) => {
		url = u;
		return new Response(JSON.stringify(body), { status });
	});
	const res = await handler({
		locals: { user: { id: 'u1' }, accessToken: 'tok' },
		fetch,
		url: new URL(`http://portal.test/notifications/x${query}`)
	} as unknown as RecentArg);
	return { res, url };
}

describe('the bell feed proxy', () => {
	it('forwards the active contact', async () => {
		const { url } = await called(recent, '?contact_id=42');
		expect(url).toContain('contact_id=42');
		expect(url).toContain('limit=');
	});

	it('sends no contact filter when there is none to send', async () => {
		// A single-contact login: narrowing by an id nobody chose would be inventing one.
		const { url } = await called(recent, '');
		expect(url).not.toContain('contact_id');
	});

	it('drops a contact id that is not a number, rather than 422ing the API', async () => {
		// The API types this `int`. A junk value is a 422, which this proxy dresses as a 502 and the
		// bell reads as "leave the rows alone" — a dropdown that quietly stops updating.
		const { url } = await called(recent, '?contact_id=nonsense');
		expect(url).not.toContain('contact_id');
	});

	it('refuses without a signed-in user', async () => {
		const res = await recent({
			locals: {},
			fetch: vi.fn(),
			url: new URL('http://portal.test/notifications/recent')
		} as unknown as RecentArg);
		expect(res.status).toBe(401);
	});

	it('forwards an upstream failure so the bell keeps the rows it has', async () => {
		const { res } = await called(recent, '?contact_id=42', { detail: 'nope' }, 500);
		expect(res.status).toBe(502);
	});
});

describe('the badge count proxy', () => {
	it('forwards the active contact', async () => {
		const { url } = await called(unreadCount, '?contact_id=7', { count: 3 });
		expect(url).toContain('/notifications/unread-count?contact_id=7');
	});

	it('sends no contact filter when there is none', async () => {
		const { url } = await called(unreadCount, '', { count: 3 });
		expect(url).toBe('http://api.test/notifications/unread-count');
	});

	it('answers 401 when signed out, never a zero the bell would show as "none"', async () => {
		const fetch = vi.fn();
		const res = await unreadCount({
			locals: { user: null, accessToken: null },
			fetch,
			url: new URL('http://portal.test/notifications/unread-count')
		} as unknown as RecentArg);
		expect(res.status).toBe(401);
		expect(fetch).not.toHaveBeenCalled();
	});
});

describe('the realtime proxy', () => {
	it('puts the active contact on the ticket path, not the stream path', async () => {
		sse.ticketPath = '';
		await events({
			locals: { user: { id: 'u1' }, accessToken: 'tok' },
			fetch: vi.fn(),
			request: new Request('http://portal.test/notifications/events'),
			url: new URL('http://portal.test/notifications/events?contact_id=9')
		} as unknown as RecentArg);
		expect(sse.ticketPath).toBe('/notifications/ticket?contact_id=9');
	});

	it('mints an unscoped ticket when no contact is named', async () => {
		sse.ticketPath = '';
		await events({
			locals: { user: { id: 'u1' }, accessToken: 'tok' },
			fetch: vi.fn(),
			request: new Request('http://portal.test/notifications/events'),
			url: new URL('http://portal.test/notifications/events')
		} as unknown as RecentArg);
		expect(sse.ticketPath).toBe('/notifications/ticket');
	});
});

describe('the contact-id rule itself', () => {
	const at = (q: string) => new URL(`http://portal.test/x${q}`);

	it('takes a positive integer', () => {
		expect(activeContactId(at('?contact_id=12'))).toBe(12);
		expect(activeContactParam(at('?contact_id=12'))).toBe('contact_id=12');
	});

	it('refuses everything that is not one', () => {
		for (const q of ['', '?contact_id=', '?contact_id=%20', '?contact_id=abc', '?contact_id=1.5'])
			expect(activeContactId(at(q))).toBeNull();
		// A serial is never 0 or negative, so neither is a real selection.
		expect(activeContactId(at('?contact_id=0'))).toBeNull();
		expect(activeContactId(at('?contact_id=-3'))).toBeNull();
		expect(activeContactParam(at('?contact_id=abc'))).toBe('');
	});
});
