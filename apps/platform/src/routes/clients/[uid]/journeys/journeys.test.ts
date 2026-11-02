/**
 * The client Journeys loader. The `cases:read` gate is a 403 thrown before anything is fetched —
 * never a silent empty list — and the two reads (this client's journeys, and the published flows
 * a starter may launch) stream as one promise, so the load returns while they are still in flight.
 * A failed journeys read resolves `loadError` rather than rejecting or pretending there are none.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

type LoadEvent = Parameters<typeof load>[0];

const JOURNEYS = { journeys: [{ uid: 'jny_1', flow_name: 'Onboarding' }] };
const FLOWS = [{ code: 'onboarding', name: 'Onboarding' }];

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});

function event(
	over: {
		permissions?: string[];
		fetch?: typeof globalThis.fetch;
		parent?: () => Promise<unknown>;
		uid?: string;
	} = {}
) {
	const fetch =
		over.fetch ??
		vi.fn(async (input: RequestInfo | URL) => {
			const url = String(input);
			if (url.includes('/flows')) return json(FLOWS);
			return json(JOURNEYS);
		});
	const uid = over.uid ?? 'con_9b2e';
	return {
		params: { uid },
		locals: {
			user: { id: 'user_1' },
			accessToken: 'token',
			permissions: over.permissions ?? ['cases:read'],
			role: 'adviser'
		},
		parent: over.parent ?? (async () => ({ detail: { contact: { uid } } })),
		fetch
	} as unknown as LoadEvent & { fetch: ReturnType<typeof vi.fn> };
}

type Loaded = Exclude<Awaited<ReturnType<typeof load>>, void>;

/** The loader's answer with the streamed book resolved — what the page sees once the rows land. */
const run = async (e: LoadEvent) => {
	const data = (await load(e)) as Loaded;
	return await data.book;
};

describe('the client journeys loader streams the book', () => {
	it('returns before the API answers, with both reads already in flight', async () => {
		// The gate and the shell are decided; the response starts. A loader that awaited either
		// read here would put that round trip back on the time to first byte. Both requests are
		// issued together — a waterfall would still be waiting on the first when this returns.
		const e = event({
			permissions: ['cases:read', 'cases:write'],
			fetch: vi.fn(() => new Promise<Response>(() => {}))
		});
		const data = (await load(e)) as Loaded;
		expect(data.book).toBeInstanceOf(Promise);
		expect(e.fetch).toHaveBeenCalledTimes(2);
		const urls = e.fetch.mock.calls.map((call) => String(call[0]));
		expect(urls.some((url) => url.includes('/crm/contacts/con_9b2e/journeys'))).toBe(true);
		expect(urls.some((url) => url.includes('/flows?status=published'))).toBe(true);
	});
});

describe('the client journeys loader gate', () => {
	it('403s without cases:read — before the shell, and without calling the API', async () => {
		const parent = vi.fn(async () => ({ detail: { contact: { uid: 'con_9b2e' } } }));
		const e = event({ permissions: ['contacts:read'], parent });
		await expect(load(e)).rejects.toMatchObject({ status: 403 });
		expect(parent).not.toHaveBeenCalled();
		expect(e.fetch).not.toHaveBeenCalled();
	});

	it('lets cases:write through (it satisfies cases:read) and offers the flows they may start', async () => {
		const book = await run(event({ permissions: ['cases:write'] }));
		expect(book.loadError).toBe(false);
		expect(book.journeys).toEqual(JOURNEYS.journeys);
		expect(book.newJourneys).toEqual([
			{
				code: 'onboarding',
				name: 'Onboarding',
				href: '/journeys/new?flow=onboarding&client=con_9b2e'
			}
		]);
	});

	it('does not ask for published flows when the caller cannot start one', async () => {
		const e = event({ permissions: ['cases:read'] });
		const book = await run(e);
		expect(e.fetch).toHaveBeenCalledTimes(1);
		expect(String(e.fetch.mock.calls[0][0])).toContain('/crm/contacts/con_9b2e/journeys');
		expect(book.newJourneys).toEqual([]);
		expect(book.loadError).toBe(false);
	});
});

describe('the client journeys loader failure modes', () => {
	it('sets loadError when the journeys read fails, rather than an empty list', async () => {
		const e = event({
			permissions: ['cases:write'],
			fetch: vi.fn(async (input: RequestInfo | URL) => {
				if (String(input).includes('/flows')) return json(FLOWS);
				return json({ detail: 'nope' }, 500);
			})
		});
		const book = await run(e);
		expect(book.journeys).toEqual([]);
		expect(book.newJourneys).toEqual([]);
		expect(book.loadError).toBe(true);
	});

	it('sets loadError when the journeys read throws, and does not reject', async () => {
		const e = event({
			fetch: vi.fn(async () => {
				throw new Error('ECONNREFUSED');
			})
		});
		const book = await run(e);
		expect(book).toEqual({ journeys: [], newJourneys: [], loadError: true });
	});

	it('keeps the journeys when only the flows read fails — that menu is optional', async () => {
		const e = event({
			permissions: ['cases:write'],
			fetch: vi.fn(async (input: RequestInfo | URL) => {
				if (String(input).includes('/flows')) return json({ detail: 'nope' }, 500);
				return json(JOURNEYS);
			})
		});
		const book = await run(e);
		expect(book.loadError).toBe(false);
		expect(book.journeys).toEqual(JOURNEYS.journeys);
		expect(book.newJourneys).toEqual([]);
	});
});
