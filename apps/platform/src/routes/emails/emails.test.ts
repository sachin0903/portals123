/**
 * The Activity loader — the firm's outbound-email feed (docs/comms.md).
 *
 * Shaped like `logs.test.ts` next door, because the two loaders share a posture: gate on
 * `activity:read`, ask for page one plus the filter vocabulary exactly once, and degrade to a
 * banner rather than to an empty page when the API is unreachable.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

type LoadEvent = Parameters<typeof load>[0];

const FACETS = {
	statuses: ['sent', 'delivered'],
	template_codes: ['document_sent'],
	event_types: ['document.sent'],
	categories: ['documents']
};

const PAGE = {
	items: [{ uid: 'abc123abc123', id: 41 }],
	has_more: true,
	facets: FACETS,
	sending_disabled: null
};

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function event(over: { permissions?: string[]; fetch?: typeof globalThis.fetch; url?: URL } = {}) {
	const fetch = over.fetch ?? vi.fn(async () => json(PAGE));
	return {
		url: over.url ?? new URL('http://portal.test/emails'),
		locals: {
			user: { id: 'user_1' },
			accessToken: 'token',
			permissions: over.permissions ?? ['activity:read'],
			role: 'adviser'
		},
		fetch
	} as unknown as LoadEvent & { fetch: ReturnType<typeof vi.fn> };
}

type Loaded = Exclude<Awaited<ReturnType<typeof load>>, void>;

/** The loader's answer with the streamed feed resolved — what the page sees once the rows land. */
const run = async (e: LoadEvent) => {
	const data = (await load(e)) as Loaded;
	return { ...(await data.feed), eventType: data.eventType };
};

describe('the /emails loader streams the feed', () => {
	it('returns before the API answers, with the feed as a promise and the deep link in hand', async () => {
		// The gates and the inbound `?event_type=` are decided synchronously, so the toolbar can open
		// already narrowed while the rows are still on their way.
		const e = event({
			fetch: vi.fn(() => new Promise<Response>(() => {})),
			url: new URL('http://portal.test/emails?event_type=document.sent')
		});
		const data = (await load(e)) as Loaded;
		expect(data.feed).toBeInstanceOf(Promise);
		expect(data.eventType).toBe('document.sent');
	});
});

describe('the /emails loader gate', () => {
	it('bounces an unauthenticated caller to /login', async () => {
		const e = event();
		(e.locals as { user: unknown }).user = null;
		await expect(run(e)).rejects.toMatchObject({ status: 303 });
	});

	it('refuses a caller without activity:read', async () => {
		await expect(run(event({ permissions: [] }))).rejects.toMatchObject({ status: 403 });
	});

	it('accepts activity:read', async () => {
		const data = await run(event());
		expect(data.messages).toHaveLength(1);
	});
});

describe('the /emails loader asks for the first page and the vocabulary, once', () => {
	it('requests facets on entry', async () => {
		const e = event();
		await run(e);
		const url = String(e.fetch.mock.calls[0][0]);
		expect(url).toContain('/firm/comms/messages?');
		expect(url).toContain('facets=true');
	});

	it('sends no filters — the loader always reads the newest unfiltered page', async () => {
		// Filters are applied server-side through `/emails/more` and never live in the URL, so a
		// reload must not resurrect one. The same doctrine the logs surface follows.
		const e = event();
		await run(e);
		const url = String(e.fetch.mock.calls[0][0]);
		for (const param of ['status=', 'q=', 'template_code=', 'before=']) {
			expect(url).not.toContain(param);
		}
	});

	it('carries has_more and the facets through', async () => {
		const data = await run(event());
		expect(data.hasMore).toBe(true);
		expect(data.facets).toEqual(FACETS);
	});

	it('reads a missing facets key defensively rather than failing', async () => {
		const fetch = vi.fn(async () => json({ items: [], has_more: false }));
		const data = await run(event({ fetch }));
		expect(data.facets).toBeNull();
		expect(data.apiError).toBeNull();
	});
});

describe('the /emails loader failure modes', () => {
	it('degrades to a banner when the API is unreachable, not to an empty feed', async () => {
		const fetch = vi.fn(async () => {
			throw new Error('down');
		});
		const data = await run(event({ fetch }));
		expect(data.messages).toEqual([]);
		// The banner is what stops "no mail sent" being read off an outage.
		expect(data.apiError).toContain('Could not load sent email');
	});

	it('gives an action when access is refused', async () => {
		const fetch = vi.fn(async () => json({ detail: 'nope' }, 403));
		const data = await run(event({ fetch }));
		expect(data.apiError).toContain('Ask a firm administrator');
	});
});

describe('the send-guard banner', () => {
	it('forwards a flag and never the configuration behind it', async () => {
		// The whole feed reads "Not sent"; without this the page says every email failed. What must
		// NOT travel is the reason: page data is serialised into the HTML, so forwarding it would
		// publish the setting names whether or not anything renders them.
		const reason = "APP_STAGE='dev' is not production and EMAIL_ALLOWLIST is empty";
		const fetch = vi.fn(async () => json({ ...PAGE, sending_disabled: reason }));
		const data = await run(event({ fetch }));
		expect(data.sendingDisabled).toBe(true);
		expect(JSON.stringify(data)).not.toContain('APP_STAGE');
		expect(JSON.stringify(data)).not.toContain('EMAIL_ALLOWLIST');
	});

	it('is absent when sending works', async () => {
		const data = await run(event());
		expect(data.sendingDisabled).toBe(false);
	});

	it('comes from the feed response, never from the health probe', async () => {
		// `/health/ready` is infrastructure: out of the OpenAPI schema, 503s on unrelated outages,
		// and no portal calls it. One request, and it is the feed's own.
		const e = event();
		await run(e);
		expect(e.fetch).toHaveBeenCalledTimes(1);
		expect(String(e.fetch.mock.calls[0][0])).not.toContain('/health');
	});
});

describe('the registry’s count links here narrowed', () => {
	/**
	 * The event registry shows a 30-day email count per event and links it to
	 * `/emails?event_type=…`. The loader ignored search params entirely — deliberately, since
	 * filters are never WRITTEN to the URL — so the count landed on an unfiltered feed. Reading an
	 * inbound deep link is the one exception, and it is inbound only.
	 */
	it('forwards event_type to the API and reports it back', async () => {
		const e = event({ url: new URL('http://portal.test/emails?event_type=document.sent') });
		const data = await run(e);
		const urls = e.fetch.mock.calls.map((c: unknown[]) => String(c[0]));
		expect(urls.some((u: string) => u.includes('event_type=document.sent'))).toBe(true);
		expect(data.eventType).toBe('document.sent');
	});

	it('opens unfiltered with no deep link', async () => {
		const e = event();
		const data = await run(e);
		const urls = e.fetch.mock.calls.map((c: unknown[]) => String(c[0]));
		expect(urls.some((u: string) => u.includes('event_type'))).toBe(false);
		expect(data.eventType).toBe('');
	});

	it('caps what it forwards', async () => {
		// It lands in an API query. The endpoint validates it too, but there is no reason to forward
		// whatever was pasted into the address bar.
		const long = 'a'.repeat(400);
		const e = event({ url: new URL(`http://portal.test/emails?event_type=${long}`) });
		const data = await run(e);
		expect(data.eventType.length).toBe(100);
	});
});
