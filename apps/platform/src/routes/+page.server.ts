import { redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, fetchJson, type LoadResult } from '$lib/api';
import type { AppExecutionRow } from '@graphene/ui/book/journeys';
import type { ReviewRow } from '@graphene/ui/book/reviews';
import type { Dash, DashClientRow, FirmOverview, StaffNote } from '@graphene/ui/book/dashboard';
import { apiParams, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import type { PageServerLoad, Actions } from './$types';

type ClientRow = DashClientRow;

const FILTER_KEYS = ['status', 'adviser', 'type'] as const;

export const load: PageServerLoad = async ({ locals, fetch, url, depends }) => {
	if (!locals.user) redirect(303, '/login');
	depends('app:dashboard');

	// The left column - see `ListingQuery.defaultSort`.
	const query = queryFromParams(url.searchParams, FILTER_KEYS, { sort: 'name', dir: 'asc' });
	// Nothing here is awaited: the permission answers are synchronous and both reads stream. The
	// overview is its OWN promise, not a member of `dash`, so the KPI tiles paint the moment the
	// summary query answers instead of waiting for the slowest of the four reads (see `dash.ts`).
	// Cash and growth travel on the contacts row, from the stored valuation.
	const contactParams = new URLSearchParams(apiParams(query));
	const clientsP = fetchJson<ListingPage<ClientRow> | ClientRow[]>(
		fetch,
		`${apiUrl()}/crm/contacts?${contactParams}`,
		authHeaders(locals.accessToken)
	);
	return {
		query,
		canReviews: true,
		canJourneys: can(locals.permissions, locals.role, 'cases:read'),
		canManage: can(locals.permissions, locals.role, 'contacts:write'),
		overview: loadOverview(locals.accessToken, fetch),
		dash: loadDash(locals.accessToken, fetch, clientsP, { canReviews: true })
	};
};

async function loadOverview(
	accessToken: string | null,
	fetchFn: typeof fetch
): Promise<FirmOverview | null> {
	const res = await fetchJson<FirmOverview>(
		fetchFn,
		`${apiUrl()}/crm/overview`,
		authHeaders(accessToken)
	);
	return res.ok ? res.data : null;
}

async function loadDash(
	accessToken: string | null,
	fetchFn: typeof fetch,
	clientsP: Promise<LoadResult<ListingPage<ClientRow> | ClientRow[]>>,
	opts: { canReviews: boolean }
): Promise<Dash> {
	const headers = authHeaders(accessToken);
	const [clientsR, journeysR, reviewsR, notesR] = await Promise.all([
		clientsP,
		// The caller's OWN onboarding, decided in the API (`mine`): the portal knows the caller by
		// their WorkOS id and the assignee by their internal user id, so it cannot filter this
		// itself. The journeys PAGE stays the firm's whole board.
		fetchJson<AppExecutionRow[]>(fetchFn, `${apiUrl()}/applications?mine=true`, headers),
		opts.canReviews
			? fetchJson<ReviewRow[]>(fetchFn, `${apiUrl()}/reviews?scope=inbox&limit=10`, headers)
			: Promise.resolve<LoadResult<ReviewRow[]>>({ ok: true, data: [] }),
		// The caller's own notes — RLS admits the owner alone, so there is nothing to filter here.
		fetchJson<StaffNote[]>(fetchFn, `${apiUrl()}/me/notes`, headers)
	]);

	const book: ListingPage<ClientRow> = clientsR.ok
		? Array.isArray(clientsR.data)
			? { items: clientsR.data, total: clientsR.data.length, facets: {} }
			: clientsR.data
		: { items: [], total: 0, facets: {} };
	return {
		clients: book.items,
		matchCount: book.total,
		facets: book.facets,
		// The slider track — see `ListingPage.bounds`.
		bounds: 'bounds' in book ? (book.bounds ?? {}) : {},
		// TWO different failures, and folding them lost the one the reader can act on. A 403 is
		// "this login may not read the book" — permanent, nothing to retry, ask an administrator;
		// anything else is an outage that a retry can fix.
		clientsError: !clientsR.ok && clientsR.status !== 403,
		clientsForbidden: !clientsR.ok && clientsR.status === 403,
		journeys: journeysR.ok ? journeysR.data : [],
		journeysError: !journeysR.ok && journeysR.status !== 403,
		reviews: reviewsR.ok ? reviewsR.data : [],
		reviewsError: opts.canReviews && !reviewsR.ok,
		notes: notesR.ok ? notesR.data : [],
		notesError: !notesR.ok
	};
}

export const actions: Actions = {};
