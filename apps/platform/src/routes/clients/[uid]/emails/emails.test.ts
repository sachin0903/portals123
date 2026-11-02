/**
 * The client Activity section: its gate, and — the point of the section — its SCOPE.
 *
 * A record feed that answers with the firm's rows under one client's name is the defect this
 * section's sibling shipped with (`logs.test.ts` records it), so the scoping cases here are
 * behavioural rather than a comment: the contact must be the route's own uid, must survive whatever
 * the URL carries, and must be the proxy's to set on every later page.
 */
import { describe, expect, it, vi } from 'vitest';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { load } from './+page.server';
import { GET } from './more/+server';
import { load as detailLoad } from './[messageUid]/+page.server';

/** Derived from `load` rather than imported: `@sveltejs/kit`'s `LoadEvent` is the CLIENT one, and a
 *  server load takes `cookies`/`locals`/`platform` it does not have. The sibling logs test does the
 *  same. */
type LoadEvent = Parameters<typeof load>[0];
type ProxyEvent = Parameters<typeof GET>[0];

const FACETS = { statuses: ['delivered'], template_codes: ['invite'], categories: [] };
const PAGE = { items: [{ id: 3 }], has_more: true, facets: FACETS, sending_disabled: null };

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function event(over: {
	search?: string;
	canReadActivity?: boolean;
	user?: unknown;
	fetch?: typeof globalThis.fetch;
}) {
	const fetch = over.fetch ?? vi.fn(async () => json(PAGE));
	const parent = vi.fn(async () => ({
		canReadActivity: over.canReadActivity ?? true,
		clientName: 'Margaret Cole'
	}));
	return {
		params: { uid: 'con_9b2e' },
		url: new URL(`http://firm.test/clients/con_9b2e/emails${over.search ?? ''}`),
		locals: {
			user: over.user === undefined ? { id: 'staff_1' } : over.user,
			accessToken: 'tok'
		},
		fetch,
		parent
	} as unknown as LoadEvent & { fetch: ReturnType<typeof vi.fn> };
}

const run = async (e: LoadEvent) => {
	const loaded = (await load(e)) as Exclude<Awaited<ReturnType<typeof load>>, void>;
	return { ...loaded, ...(await loaded.feed) };
};

describe('the client activity loader gate', () => {
	it('bounces an unauthenticated caller to /login', async () => {
		await expect(load(event({ user: null }))).rejects.toMatchObject({
			status: 303,
			location: '/login'
		});
	});

	it('403s without activity:read — and never calls the API', async () => {
		const e = event({ canReadActivity: false });
		await expect(load(e)).rejects.toMatchObject({ status: 403 });
		expect(e.fetch).not.toHaveBeenCalled();
	});
});

describe('the client activity loader scoping', () => {
	it('sends the route’s uid as the contact, the page size and the facets flag', async () => {
		const e = event({});
		await run(e);
		const called = new URL(String(e.fetch.mock.calls[0][0]));
		expect(called.pathname).toContain('/firm/comms/messages');
		expect([...called.searchParams.keys()].sort()).toEqual(['contact', 'facets', 'limit']);
		expect(called.searchParams.get('contact')).toBe('con_9b2e');
		expect(called.searchParams.get('limit')).toBe(String(PAGE_SIZE));
	});

	it('cannot be widened or re-pointed by a contact param in the URL', async () => {
		const e = event({ search: '?contact=con_other&status=bounced&limit=500' });
		await run(e);
		const called = new URL(String(e.fetch.mock.calls[0][0]));
		// Nothing is read from the URL here: the scope is the route segment, and the filters are
		// applied in place from now on (so a stale address can neither narrow nor widen the feed).
		expect(called.searchParams.get('contact')).toBe('con_9b2e');
		expect(called.searchParams.get('status')).toBeNull();
		expect(called.searchParams.get('limit')).toBe(String(PAGE_SIZE));
	});

	it('carries the API’s own has_more, facets and send-guard reason through', async () => {
		const data = await run(event({}));
		expect(data.hasMore).toBe(true);
		expect(data.facets).toEqual(FACETS);
		expect(data.client).toEqual({ uid: 'con_9b2e', name: 'Margaret Cole' });
	});

	it('reports an API failure rather than showing an empty feed', async () => {
		const fetch = vi.fn(async () => json({ detail: 'nope' }, 500));
		const data = await run(event({ fetch: fetch as unknown as typeof globalThis.fetch }));
		expect(data.messages).toEqual([]);
		expect(data.apiError).toBeTruthy();
	});
});

describe('the client activity paging proxy', () => {
	const proxy = (search: string, user: unknown = { id: 'staff_1' }) => {
		// The double DECLARES its url parameter so the recorded call is readable — a parameterless
		// `vi.fn` types `mock.calls` as an empty tuple and the assertions below cannot index it.
		const urls: string[] = [];
		const fetch = vi.fn(async (url: string | URL) => {
			urls.push(String(url));
			return json(PAGE);
		});
		return {
			urls,
			fetch,
			call: () =>
				GET({
					params: { uid: 'con_9b2e' },
					url: new URL(`http://firm.test/clients/con_9b2e/emails/more${search}`),
					locals: { user, accessToken: 'tok' },
					fetch: fetch as unknown as typeof globalThis.fetch
				} as unknown as ProxyEvent)
		};
	};

	it('401s without a session', async () => {
		const p = proxy('', null);
		expect((await p.call()).status).toBe(401);
		expect(p.fetch).not.toHaveBeenCalled();
	});

	it('forwards the filters it knows and sets the contact itself', async () => {
		const p = proxy('?before=90&status=bounced&q=invite');
		await p.call();
		const called = new URL(p.urls[0]);
		expect(called.searchParams.get('before')).toBe('90');
		expect(called.searchParams.get('status')).toBe('bounced');
		expect(called.searchParams.get('q')).toBe('invite');
		expect(called.searchParams.get('contact')).toBe('con_9b2e');
	});

	it('refuses to be pointed at another client, or at the whole firm', async () => {
		// `contact` is set AFTER the forwarding loop and is not on the forwarded list, so neither a
		// substituted nor a blanked value in the query survives.
		const p = proxy('?contact=con_other&tenant=t_other');
		await p.call();
		const called = new URL(p.urls[0]);
		expect(called.searchParams.get('contact')).toBe('con_9b2e');
		expect(called.searchParams.get('tenant')).toBeNull();
	});
});

describe('the client activity message detail', () => {
	const MESSAGE = {
		uid: 'msg_1',
		subject: 'Your annual review',
		recipient_contact_uid: 'con_9b2e'
	};

	const detailEvent = (over: {
		messageUid?: string;
		body?: unknown;
		status?: number;
		canReadActivity?: boolean;
		user?: unknown;
	}) => {
		const fetch = vi.fn(async () => json(over.body ?? MESSAGE, over.status ?? 200));
		return {
			fetch,
			event: {
				params: { uid: 'con_9b2e', messageUid: over.messageUid ?? 'msg_1' },
				locals: {
					user: over.user === undefined ? { id: 'staff_1' } : over.user,
					accessToken: 'tok'
				},
				fetch,
				parent: vi.fn(async () => ({
					canReadActivity: over.canReadActivity ?? true,
					clientName: 'Margaret Cole'
				}))
			}
		};
	};

	it('opens a message that belongs to this client', async () => {
		const { event } = detailEvent({});
		const data = (await detailLoad(event as never)) as { client: { name: string } };
		expect(data.client).toEqual({ uid: 'con_9b2e', name: 'Margaret Cole' });
	});

	it('403s without activity:read, and never fetches the message', async () => {
		const { event, fetch } = detailEvent({ canReadActivity: false });
		await expect(detailLoad(event as never)).rejects.toMatchObject({ status: 403 });
		expect(fetch).not.toHaveBeenCalled();
	});

	it('REFUSES another client’s message, though the firm may read it', async () => {
		// The API scopes a message to the FIRM, so this one loads fine — it is simply not this
		// client's. Rendering it here would put another client's subject, body and email address
		// under the name in the sidebar.
		const { event } = detailEvent({
			body: { ...MESSAGE, uid: 'msg_2', recipient_contact_uid: 'con_other' }
		});
		const loaded = (await detailLoad(event as never)) as { record: Promise<unknown> };
		await expect(loaded.record).rejects.toMatchObject({ status: 404 });
	});

	it('refuses a message that names no contact at all', async () => {
		// Unattributable rather than another client's — this section only shows mail it can prove
		// belongs here.
		const { event } = detailEvent({ body: { ...MESSAGE, recipient_contact_uid: null } });
		const loaded = (await detailLoad(event as never)) as { record: Promise<unknown> };
		await expect(loaded.record).rejects.toMatchObject({ status: 404 });
	});
});
