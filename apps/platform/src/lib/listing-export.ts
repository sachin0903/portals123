import { apiUrl, authHeaders } from '$lib/api';
import { apiParams, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';

/**
 * Collecting the whole of a filtered listing for an export.
 *
 * **The export is the match set, not the page.** Every listing here is server-paged at 25 rows, so
 * exporting "the rows" would hand someone 25 of 400 in a file that looks complete — the same class
 * of wrong answer as filtering client-side, and worse, because a spreadsheet carries no hint that
 * it was truncated. So the route pages the API until it has everything the filters match.
 *
 * The filters come from the page's own URL through `queryFromParams`/`apiParams`, the same pair the
 * loader uses, so the file can never disagree with the screen about what was asked for.
 */

/** Thrown when the API refuses or fails mid-collection; `status` is its own. */
export class ExportFailed extends Error {
	constructor(readonly status: number) {
		super(`export failed: ${status}`);
		this.name = 'ExportFailed';
	}
}

/** Rows per API request while collecting. Larger than the on-screen page: nobody is waiting on a
 *  first paint here, and it is the round trips that cost. */
const PAGE = 200; // `app/listing.py::PAGE_LIMIT_MAX` — asking for more is silently clamped.

/** Pages requested at once. Each is a full listing query on the API, so this stays small. */
const IN_FLIGHT = 4;

/**
 * A ceiling, so a mis-specified export cannot walk a whole book into memory and hold a request
 * open while it does. Reported rather than silently applied — see `truncated`.
 */
export const MAX_ROWS = 20_000;

export interface Collected<T> {
	rows: T[];
	/** True when the cap stopped us short of `total`. The caller must SAY so in the file. */
	truncated: boolean;
	/** What the server said the filters match, before the cap. */
	total: number;
	/**
	 * True when the ENDPOINT's own read ceiling may have withheld rows before it ever counted them.
	 *
	 * Distinct from `truncated`, and it has to be: `truncated` is a shortfall against a `total` we
	 * trust, and this is the case where the total ITSELF is the ceiling. Listings that page in SQL
	 * (`/crm/transactions` with `paged=true`) report the real match count, so this flag stays off
	 * unless the caller names an endpoint that still has its own read ceiling.
	 */
	capped: boolean;
}

/**
 * Page `path` until the filtered set is exhausted (or the cap is hit).
 *
 * `extra` is for the params a route adds that are not listing filters — `paged=true`, a
 * `contact_id`, a tenant target.
 *
 * `serverCap` is the endpoint's OWN read ceiling, where it has one. See `Collected.capped`.
 */
export async function collectAll<T>(
	fetcher: typeof fetch,
	accessToken: string | null | undefined,
	path: string,
	params: URLSearchParams,
	extra: Record<string, string> = {},
	serverCap?: number
): Promise<Collected<T>> {
	let total = 0;
	const fetchPage = async (offset: number): Promise<T[]> => {
		const query = new URLSearchParams(params);
		for (const [key, value] of Object.entries(extra)) query.set(key, value);
		query.set('limit', String(PAGE));
		query.set('offset', String(offset));

		const response = await fetcher(`${apiUrl()}${path}?${query}`, {
			headers: authHeaders(accessToken ?? null)
		});
		// A partial file is worse than none: it looks like the answer. Fail the whole export — and
		// carry the STATUS, so a refusal reaches the reader as a refusal. Collapsing a 403 into a
		// generic failure tells someone their export is broken when the truth is they may not have
		// it, and they retry instead of asking for access.
		if (!response.ok) throw new ExportFailed(response.status);
		const page: ListingPage<T> = await response.json();
		if (offset === 0) total = page.total ?? page.items.length;
		return page.items;
	};

	// The first page says how many there are; the rest are fetched a few at a time, not one by one.
	const pages: T[][] = [await fetchPage(0)];
	const offsets: number[] = [];
	for (let offset = PAGE; offset < Math.min(total, MAX_ROWS); offset += PAGE) offsets.push(offset);

	// `stop` on an empty page is the belt to `total`'s braces: a server that reports a total it
	// cannot actually page to would otherwise be asked for every page it claims.
	let next = 0;
	let stop = pages[0].length === 0;
	const worker = async () => {
		while (!stop && next < offsets.length) {
			const index = next++;
			try {
				pages[index + 1] = await fetchPage(offsets[index]);
			} catch (err) {
				stop = true;
				throw err;
			}
			if (pages[index + 1].length === 0) stop = true;
		}
	};
	await Promise.all(Array.from({ length: Math.min(IN_FLIGHT, offsets.length) }, worker));

	// In page order, up to the first page that came back empty or was never asked for.
	const rows: T[] = [];
	for (const page of pages) {
		if (!page?.length) break;
		rows.push(...page);
	}
	const truncated = rows.length > MAX_ROWS || (rows.length >= MAX_ROWS && rows.length < total);
	// `>=`, not `===`: the ceiling is the endpoint's, and reaching it is the signal. Whether it
	// withheld anything is unknowable from here — a book with exactly 2,000 movements reports the
	// same total as one with 13,000 — so the file says "at least", never a number it cannot stand up.
	const capped = serverCap != null && total >= serverCap;
	return { rows: rows.slice(0, MAX_ROWS), truncated, total, capped };
}

/** The sort a listing's loader applies when the URL names none. */
export interface ListingSort {
	sort: string;
	dir: 'asc' | 'desc';
}

/**
 * The listing filters and sort from a page URL, as API params — minus paging, which `collectAll`
 * owns.
 *
 * `sort` is the LOADER's default, and must be the same object the loader passes: a URL with no
 * `sort` means "the page's default order", and without it the export took the API's own order —
 * so the file's rows came out in a different order from the table's.
 */
export function exportParams(
	url: URL,
	keys: readonly string[],
	sort?: ListingSort
): URLSearchParams {
	const params = apiParams(queryFromParams(url.searchParams, keys, sort ?? {}));
	params.delete('limit');
	params.delete('offset');
	return params;
}

/**
 * The active filters, in words, for the file's own subtitle.
 *
 * A short export is indistinguishable from a broken one: 64 rows out of a 583-row ledger reads as
 * missing data unless the file says it was filtered to them. The screen has the controls to explain
 * itself; a CSV on someone's desk next month has only what is written in it.
 */
export function describeFilters(params: URLSearchParams): string {
	const LABELS: Record<string, string> = {
		q: 'Search',
		status: 'Status',
		type: 'Type',
		adviser: 'Adviser',
		account: 'Account',
		model: 'Model',
		date_from: 'From',
		date_to: 'To'
	};
	const parts: string[] = [];
	for (const [key, label] of Object.entries(LABELS)) {
		const value = params.get(key)?.trim();
		if (value) parts.push(`${label}: ${value}`);
	}
	return parts.join(' · ');
}

/** The subtitle every export shares: what it holds, and what it was narrowed to. */
export function exportSubtitle(collected: Collected<unknown>, params: URLSearchParams): string {
	const filters = describeFilters(params);
	const count = collected.truncated
		? `First ${collected.rows.length.toLocaleString()} of ${collected.total.toLocaleString()} rows. Narrow the filters to include the rest`
		: collected.capped
			? // "at least", because the endpoint stopped counting at its ceiling: this file may be
				// complete or may be the first 2,000 of thirteen thousand, and it cannot tell which. A
				// bare "2,000 rows" here would be the silent short export this whole module exists to
				// prevent — it just fails one level further up than the row cap does.
				`First ${collected.rows.length.toLocaleString()} rows of at least ${collected.total.toLocaleString()}. Narrow the filters to include the rest`
			: `${collected.total.toLocaleString()} rows`;
	return filters ? `${count} · ${filters}` : count;
}
