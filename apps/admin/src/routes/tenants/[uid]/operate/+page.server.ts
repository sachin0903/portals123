import { redirect } from '@sveltejs/kit';
import type { AppExecutionRow } from '@graphene/ui/book/journeys';
import type { ReviewRow } from '@graphene/ui/book/reviews';
import type { Dash, DashClientRow, FirmOverview } from '@graphene/ui/book/dashboard';
import { apiParams, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

const FILTER_KEYS = ['status', 'adviser', 'type'] as const;

/**
 * The firm's dashboard as the platform shows it — the same four reads, through `?tenant=`, into
 * the shared `FirmDashboard`.
 *
 * One deliberate difference: the rail is the FIRM's, not the caller's. The platform asks for the
 * signed-in adviser's own onboarding (`mine`) and review inbox (`scope=inbox`); a Graphene
 * operator is neither, so those would always be empty. Here it is every live journey and every
 * pending review.
 */
export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const query = queryFromParams(url.searchParams, FILTER_KEYS, { sort: 'name', dir: 'asc' });
	// The overview streams on its own, as on the platform, so the KPI tiles paint when the summary
	// answers rather than when the slowest of the four reads does (see `dash.ts`).
	const clientsP = targetJson<ListingPage<DashClientRow> | DashClientRow[]>(
		fetch,
		locals.accessToken,
		tenant.uid,
		'/crm/contacts',
		apiParams(query)
	);
	return {
		query,
		canManage: writing,
		overview: loadOverview(fetch, locals.accessToken, tenant.uid),
		dash: loadDash(fetch, locals.accessToken, tenant.uid, clientsP)
	};
};

async function loadOverview(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	tenantUid: string
): Promise<FirmOverview | null> {
	const res = await targetJson<FirmOverview>(fetch, accessToken, tenantUid, '/crm/overview');
	return res.ok ? res.data : null;
}

async function loadDash(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	tenantUid: string,
	clientsP: ReturnType<typeof targetJson<ListingPage<DashClientRow> | DashClientRow[]>>
): Promise<Dash> {
	const [clientsR, journeysR, reviewsR] = await Promise.all([
		clientsP,
		targetJson<AppExecutionRow[]>(fetch, accessToken, tenantUid, '/applications'),
		targetJson<ReviewRow[]>(fetch, accessToken, tenantUid, '/reviews', {
			scope: 'all',
			status: 'pending'
		})
	]);

	const book: ListingPage<DashClientRow> = clientsR.ok
		? Array.isArray(clientsR.data)
			? { items: clientsR.data, total: clientsR.data.length, facets: {} }
			: clientsR.data
		: { items: [], total: 0, facets: {} };
	return {
		clients: book.items,
		matchCount: book.total,
		facets: book.facets,
		bounds: 'bounds' in book ? (book.bounds ?? {}) : {},
		clientsError: !clientsR.ok && clientsR.status !== 403,
		clientsForbidden: !clientsR.ok && clientsR.status === 403,
		journeys: journeysR.ok ? journeysR.data : [],
		journeysError: !journeysR.ok && journeysR.status !== 403,
		reviews: reviewsR.ok ? reviewsR.data.slice(0, 10) : [],
		reviewsError: !reviewsR.ok && reviewsR.status !== 403,
		// No notes: this is an elevated read of the firm, not a person's own dashboard.
		notes: null,
		notesError: false
	};
}
