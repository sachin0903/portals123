import { describe, expect, it, vi } from 'vitest';
import { collectAll, exportParams, exportSubtitle, ExportFailed, MAX_ROWS } from './listing-export';

/** A fake API that pages a fixed set, recording the requests it was asked for. */
function api(total: number, opts: { status?: number; lyingTotal?: number } = {}) {
	const seen: URLSearchParams[] = [];
	const fetcher = vi.fn(async (input: string | URL | Request) => {
		const url = new URL(String(input), 'http://x');
		seen.push(url.searchParams);
		if (opts.status) return new Response('', { status: opts.status });
		const limit = Number(url.searchParams.get('limit'));
		const offset = Number(url.searchParams.get('offset'));
		const items = Array.from({ length: Math.max(0, Math.min(limit, total - offset)) }, (_, i) => ({
			id: offset + i
		}));
		return new Response(JSON.stringify({ items, total: opts.lyingTotal ?? total, facets: {} }), {
			headers: { 'content-type': 'application/json' }
		});
	});
	return { fetcher: fetcher as unknown as typeof fetch, seen };
}

describe('collectAll', () => {
	it('collects the whole match set, not the page on screen', async () => {
		// The point of the module. Listings page at 25; exporting "the rows" would hand someone 25 of
		// 400 in a file that looks complete.
		const { fetcher, seen } = api(430);
		const out = await collectAll(fetcher, 'tok', '/crm/contacts', new URLSearchParams());
		expect(out.rows).toHaveLength(430);
		expect(out.total).toBe(430);
		expect(out.truncated).toBe(false);
		// 200 a time (the server's own PAGE_LIMIT_MAX), so three requests.
		expect(seen.map((s) => s.get('offset'))).toEqual(['0', '200', '400']);
	});

	it('makes one request when everything fits', async () => {
		const { fetcher, seen } = api(12);
		const out = await collectAll(fetcher, 'tok', '/crm/contacts', new URLSearchParams());
		expect(out.rows).toHaveLength(12);
		expect(seen).toHaveLength(1);
	});

	it('carries the filters into every page', async () => {
		// Page two of a filtered export must still be filtered — the same trap the listing pager has.
		const { fetcher, seen } = api(250);
		await collectAll(fetcher, 'tok', '/crm/contacts', new URLSearchParams({ status: 'active' }));
		expect(seen).toHaveLength(2);
		expect(seen.every((s) => s.get('status') === 'active')).toBe(true);
	});

	it('stops on an empty page even when the server over-reports the total', async () => {
		// Belt to `total`'s braces: a server claiming more rows than it can page to would otherwise
		// spin here for ever, holding the request open.
		const { fetcher } = api(50, { lyingTotal: 10_000 });
		const out = await collectAll(fetcher, 'tok', '/crm/contacts', new URLSearchParams());
		expect(out.rows).toHaveLength(50);
	});

	it('caps the rows and SAYS it capped them', async () => {
		// Silent truncation is the failure this whole module exists to avoid; the caller has to be
		// able to put it in the file.
		const { fetcher } = api(MAX_ROWS + 500);
		const out = await collectAll(fetcher, 'tok', '/crm/contacts', new URLSearchParams());
		expect(out.rows).toHaveLength(MAX_ROWS);
		expect(out.truncated).toBe(true);
		expect(out.total).toBe(MAX_ROWS + 500);
	});

	it('fails the whole export rather than returning a short file', async () => {
		const { fetcher } = api(100, { status: 500 });
		await expect(
			collectAll(fetcher, 'tok', '/crm/contacts', new URLSearchParams())
		).rejects.toBeInstanceOf(ExportFailed);
	});

	it('asks for several pages at once, in order', async () => {
		let inFlight = 0;
		let most = 0;
		const { fetcher: inner } = api(2_000);
		const fetcher = (async (input: string | URL | Request) => {
			most = Math.max(most, ++inFlight);
			await new Promise((r) => setTimeout(r, 1));
			inFlight--;
			return inner(input);
		}) as typeof fetch;
		const out = await collectAll<{ id: number }>(fetcher, 'tok', '/x', new URLSearchParams());
		expect(most).toBe(4);
		expect(out.rows.map((r) => r.id)).toEqual(Array.from({ length: 2_000 }, (_, i) => i));
	});

	it('carries the refusal status, so a 403 does not read as an outage', async () => {
		// Collapsing this into a generic failure tells someone their export is broken when the truth
		// is they may not have it — and they retry instead of asking for access.
		const { fetcher } = api(100, { status: 403 });
		await expect(
			collectAll(fetcher, 'tok', '/crm/contacts', new URLSearchParams())
		).rejects.toMatchObject({ status: 403 });
	});
});

describe('exportParams', () => {
	const KEYS = ['status', 'adviser'] as const;

	it('keeps the search and the filters the listing understands', () => {
		const url = new URL('http://x/clients?q=hall&status=active&adviser=RM&nonsense=1');
		const params = exportParams(url, KEYS);
		expect(params.get('q')).toBe('hall');
		expect(params.get('status')).toBe('active');
		expect(params.get('adviser')).toBe('RM');
		// A param the listing does not declare is not a filter — forwarding it would let a
		// hand-edited URL reach the API with something the page never sends.
		expect(params.get('nonsense')).toBeNull();
	});

	it('drops the paging, which collectAll owns', () => {
		// Otherwise the export inherits the page you happened to be on and starts at row 75.
		const url = new URL('http://x/clients?status=active&offset=75');
		const params = exportParams(url, KEYS);
		expect(params.get('offset')).toBeNull();
		expect(params.get('limit')).toBeNull();
	});
});

describe('the endpoint’s own read ceiling', () => {
	// An endpoint that still has its own read ceiling reports `total` at that ceiling. Every
	// page arrives, `rows.length >= total` is satisfied, and nothing in the response says
	// anything is missing. `truncated` cannot catch it: the shortfall is against a total that
	// is itself the ceiling. Callers pass `serverCap` when they know one exists.
	it('flags a total that has reached the ceiling', async () => {
		const { fetcher } = api(2000);
		const collected = await collectAll(
			fetcher,
			'tok',
			'/crm/transactions',
			new URLSearchParams(),
			{},
			2000
		);
		expect(collected.rows).toHaveLength(2000);
		expect(collected.truncated).toBe(false);
		expect(collected.capped).toBe(true);
	});

	it('says "at least", because it cannot know whether anything was withheld', () => {
		// A book with exactly 2,000 movements and one with 13,000 are indistinguishable from here,
		// so the file must not assert a count it cannot stand behind.
		const subtitle = exportSubtitle(
			{ rows: new Array(2000), truncated: false, total: 2000, capped: true },
			new URLSearchParams()
		);
		expect(subtitle).toContain('at least');
		expect(subtitle).not.toBe('2,000 rows');
	});

	it('stays quiet below the ceiling, and when no ceiling is declared', async () => {
		const { fetcher } = api(120);
		expect((await collectAll(fetcher, 'tok', '/x', new URLSearchParams(), {}, 2000)).capped).toBe(
			false
		);
		const { fetcher: f2 } = api(2000);
		expect((await collectAll(f2, 'tok', '/x', new URLSearchParams())).capped).toBe(false);
	});
});
