import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, fetchJson, jsonHeaders } from '$lib/api';
import {
	stepRefusal,
	transferActionPath,
	type ApiTransfer,
	type StageSummary
} from '@graphene/ui/book/transfers';
import { apiParams, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import type { Actions, PageServerLoad } from './$types';

/** The filters this listing understands. Named so an unrelated query param is not forwarded. */
const FILTER_KEYS = ['status', 'route', 'adviser', 'basis', 'custodian'] as const;

/** Everything the workbench has to fetch, in one streamed object. */
type Book = {
	transfers: ApiTransfer[];
	total: number;
	summary: StageSummary | null;
	facets: Record<string, string[]>;
	custodians: { code: string; name: string }[];
	loadError: boolean;
	forbidden: boolean;
};

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');

	// Search, filters and the page all live in the URL, so a narrowed list survives a reload and
	// can be shared — and the back button widens it again. The API applies them; nothing is
	// filtered here or in the component, because a filter over one page is a lie about the rest.
	//
	// The query is resolved here and the rows are STREAMED: the page renders on the click with its
	// toolbar already showing the filters in the URL. `redirect` stays out here — it must be thrown
	// before the response starts, not from a streamed promise.
	const query = queryFromParams(url.searchParams, FILTER_KEYS, { sort: 'created', dir: 'desc' });
	return { query, book: loadBook(locals.accessToken, fetch, query) };
};

async function loadBook(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	query: ReturnType<typeof queryFromParams>
): Promise<Book> {
	const result = await fetchJson<
		ListingPage<ApiTransfer> & {
			summary?: StageSummary;
			custodians?: { code: string; name: string }[];
		}
	>(fetch, `${apiUrl()}/transfers?${apiParams(query)}`, authHeaders(accessToken));
	if (!result.ok) {
		// A failed or refused read is a flag, not an error page: the workbench's own shape is
		// already on screen, and an empty list must not be able to mean "the read failed".
		return {
			transfers: [],
			total: 0,
			summary: null,
			facets: {},
			custodians: [],
			loadError: result.status !== 403,
			forbidden: result.status === 403
		};
	}
	return {
		transfers: result.data.items,
		total: result.data.total,
		summary: result.data.summary ?? null,
		facets: result.data.facets,
		custodians: result.data.custodians ?? [],
		loadError: false,
		forbidden: false
	};
}

async function detailOf(res: Response, fallback: string): Promise<string> {
	try {
		const body = (await res.json()) as { detail?: unknown };
		return typeof body.detail === 'string' ? body.detail : fallback;
	} catch {
		return fallback;
	}
}

export const actions: Actions = {
	/** Match open expectations to the credits that settled them. Shared with contributions —
	 *  one matcher, because a transfer's money and a contribution's arrive the same way. */
	match: async ({ locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const res = await fetch(`${apiUrl()}/contributions/match`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken)
		});
		if (!res.ok) {
			let detail = 'Could not run the matcher.';
			try {
				const body = (await res.json()) as { detail?: unknown };
				if (typeof body.detail === 'string') detail = body.detail;
			} catch {
				/* keep the fallback */
			}
			return fail(res.status, { message: detail });
		}
		const report = (await res.json()) as { matched: number; ambiguous: number };
		const parts = [`${report.matched} matched`];
		if (report.ambiguous > 0) parts.push(`${report.ambiguous} need a decision`);
		return { message: parts.join(', ') + '.' };
	},

	/** The panel's one next step. The workbench names it; this maps the name onto the provider call. */
	step: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '');
		const step = String(form.get('step') ?? '');
		const path = transferActionPath(step, uid);
		if (!path) return fail(400, { message: 'That action is not available.' });
		const res = await fetch(`${apiUrl()}${path}`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken)
		});
		if (!res.ok)
			return fail(res.status, { message: await detailOf(res, 'The provider call failed.') });
		const saved = (await res.json().catch(() => null)) as {
			provider_error?: unknown;
			action_status?: unknown;
		} | null;
		if (saved?.action_status === 'awaiting_approval') return { message: 'Waiting for approval.' };
		const refusal = stepRefusal(step, saved?.provider_error);
		if (refusal) return fail(409, { message: refusal });
		return { message: 'Done.' };
	},

	note: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '');
		const note = String(form.get('note') ?? '');
		const res = await fetch(`${apiUrl()}/transfers/${encodeURIComponent(uid)}/note`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ note })
		});
		if (!res.ok) {
			return fail(res.status, { message: await detailOf(res, 'Could not save the note.') });
		}
		return { message: 'Note saved.' };
	}
};
